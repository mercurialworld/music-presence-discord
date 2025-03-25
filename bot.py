# Required permissions:
# - Manage Roles (required to set and remove roles from members)
# - Use Slash Commands (required to create and register commands in guilds)
# Invite link:
# https://discord.com/api/oauth2/authorize?client_id=1236022326773022800&permissions=2415919104&scope=bot%20applications.commands

MUSIC_APP_ID = 1205619376275980288
PODCAST_APP_ID = 1292142821482172506
PLAYERS_JSON_URL = "https://live.musicpresence.app/v3/players.min.json"
MAX_USER_APP_ID_RETENTION = 60 * 60 * 24 * 30  # 30 days (in seconds)
MIN_RETENTION_UPDATE_INTERVAL = 60 * 60 * 24  # 24 hours (in seconds)

import os
import json
import re
import traceback
from time import time
from datetime import timedelta
from typing import Optional
from collections import defaultdict
from dataclasses import dataclass
import dataclasses
from enum import Enum

import discord
import pickledb
import asyncio
import aiohttp
from dotenv import load_dotenv
from memoize.configuration import DefaultInMemoryCacheConfiguration
from memoize.wrapper import memoize


def load_settings(version: int = 0) -> pickledb.PickleDB:
    settings = pickledb.load(f"settings.{version}.db", True)
    for key in ["apps", "user_apps", "roles"]:
        if not settings.exists(key):
            settings.dcreate(key)
    return settings


settings = load_settings()
load_dotenv()

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


def get_listener_role(
    guild: discord.Guild, for_role: discord.Role
) -> discord.Role | None:
    key = str(guild.id)
    if settings.dexists("roles", key):
        guild_roles = settings.dget("roles", key)
        if str(for_role.id) in guild_roles:
            listener_role_id = guild_roles[str(for_role.id)]
            listener_role = guild.get_role(listener_role_id)
            if listener_role is None:
                # the role seems to have been deleted
                del guild_roles[str(for_role.id)]
                settings.dadd("roles", (key, guild_roles))
            return listener_role
    return None


def get_listener_roles(guild: discord.Guild) -> list[discord.Role]:
    roles = []
    if settings.dexists("roles", str(guild.id)):
        guild_roles = settings.dget("roles", str(guild.id))
        modified = False
        for key, listener_role_id in guild_roles.items():
            listener_role = guild.get_role(listener_role_id)
            if listener_role is None:
                # the role seems to have been deleted
                del guild_roles[key]
                modified = True
            else:
                roles.append(listener_role)
        if modified:
            settings.dadd("roles", (str(guild.id), guild_roles))
    return roles


async def give_listener_role(member: discord.Member):
    for role in reversed(member.roles):
        listener_role = get_listener_role(member.guild, role)
        if listener_role is not None:
            if listener_role not in member.roles:
                await member.add_roles(listener_role)
            return


async def remove_all_listener_roles_from_member(member: discord.Member):
    for listener_role in get_listener_roles(member.guild):
        if listener_role in member.roles:
            await member.remove_roles(listener_role)


async def remove_listener_role_from_all(
    guild: discord.Guild, for_role: discord.Role
) -> discord.Role | None:
    listener_role = get_listener_role(guild, for_role)
    if listener_role is not None:
        for member in guild.members:
            if for_role in member.roles and listener_role in member.roles:
                await member.remove_roles(listener_role)
    return listener_role


async def remove_all_listener_roles_from_all(guild: discord.Guild):
    for listener_role in get_listener_roles(guild):
        for member in guild.members:
            if listener_role in member.roles:
                await member.remove_roles(listener_role)


@dataclass
class UserApp:
    app_id: int
    user_id: int
    timestamp: int


async def check_member(member: discord.Member):
    if member.status in (discord.Status.invisible, discord.Status.offline):
        return await remove_all_listener_roles_from_member(member)
    apps = settings.get("apps")
    user_apps = {}
    if settings.dexists("user_apps", str(member.id)):
        user_apps = settings.dget("user_apps", str(member.id))
    for activity in member.activities:
        if (
            not isinstance(activity, discord.Spotify)
            and isinstance(activity, discord.Activity)
            and (
                str(activity.application_id) in apps
                or str(activity.application_id) in user_apps
            )
        ):
            app_id_key = str(activity.application_id)
            if app_id_key in user_apps:
                # Update the timestamp to the current time
                # since this user app ID was used now.
                info = UserApp(**user_apps[app_id_key])
                now = int(time())
                if info.timestamp + MIN_RETENTION_UPDATE_INTERVAL < now:
                    # Make sure it's not updated too frequently though
                    info.timestamp = now
                    user_apps[app_id_key] = dataclasses.asdict(info)
                    settings.dadd("user_apps", (str(member.id), user_apps))
            return await give_listener_role(member)
    await remove_all_listener_roles_from_member(member)


async def check_guild(guild: discord.Guild):
    if settings.dexists("roles", str(guild.id)):
        for member in guild.members:
            await check_member(member)


async def check_guilds():
    for guild in client.guilds:
        await check_guild(guild)


async def setup_guild(guild: discord.Guild):
    tree.copy_global_to(guild=guild)
    commands = await tree.sync(guild=guild)
    print(f"Synced {len(commands)} commands: {', '.join([ c.name for c in commands ])}")


async def purge_user_app_ids():
    apps = settings.get("apps")
    user_apps = settings.get("user_apps")
    sanitized = {}
    for user_id, value in user_apps.items():
        result = {}
        for app_id, info in value.items():
            # Remove app ids that are already known
            if str(app_id) in apps:
                print(f"Deleted known user app ID {app_id} for user {user_id}")
                continue
            # Remove app ids that are past their max age
            parsed_info = UserApp(**info)
            print("parsed_info", parsed_info)
            if parsed_info.timestamp + MAX_USER_APP_ID_RETENTION < int(time()):
                print(f"Deleted expired user app ID {app_id} for user {user_id}")
                continue
            result[str(app_id)] = info
        if len(result) > 0:
            sanitized[user_id] = result
    settings.set("user_apps", sanitized)


async def update_apps():
    result = {}
    # TODO clean this up
    result[str(MUSIC_APP_ID)] = True
    result[str(PODCAST_APP_ID)] = True
    async with aiohttp.ClientSession() as session:
        async with session.get(PLAYERS_JSON_URL) as response:
            if response.status != 200:
                print("failed to download players from", PLAYERS_JSON_URL)
                return
            players = json.loads(await response.read())
            for player in players["players"]:
                if "extra" in player and "discord_application_id" in player["extra"]:
                    app_id = player["extra"]["discord_application_id"]
                    result[str(app_id)] = True
                else:
                    print("player", player, "does not have a discord app id")
    settings.set("apps", result)
    print(f"Updated application IDs ({len(result)} entries)")
    await purge_user_app_ids()


async def update_apps_periodically():
    while True:
        print("Updating application IDs")
        await update_apps()
        await check_guilds()
        await asyncio.sleep(60 * 60 * 8)


@client.event
async def on_ready():
    for guild in client.guilds:
        await setup_guild(guild)
    client.loop.create_task(update_apps_periodically())


@client.event
async def on_guild_join(guild: discord.Guild):
    await setup_guild(guild)


@client.event
async def on_guild_remove(guild: discord.Guild):
    if settings.dexists("roles", str(guild.id)):
        settings.dpop("roles", str(guild.id))


@client.event
async def on_presence_update(_: discord.Member, member: discord.Member):
    await check_member(member)


# Technically we should observe updates to roles
# that the listener roles depend on too but that happens so infrequently,
# we might as well wait until the presence has been updated.


def rreplace(s: str, old: str, new: str, occurrence: int = 1):
    li = s.rsplit(old, occurrence)
    return new.join(li)


def get_role_overview(guild: discord.Guild) -> str | None:
    if not settings.dexists("roles", str(guild.id)):
        return None
    inverse = defaultdict(list)
    guild_roles = settings.dget("roles", str(guild.id))
    for for_role_id, listener_role_id in guild_roles.items():
        inverse[listener_role_id].append(for_role_id)
    lines = []
    for listener_role_id, for_role_ids in inverse.items():
        lines.append(
            f"- <@&{listener_role_id}> is assigned to {rreplace(', '.join([
                f"<@&{role_id}>" for role_id in for_role_ids
            ]), ', ', ' and ')}"
        )
    return "\n".join(lines)


@tree.command(
    name="role",
    description="Set or unset the role to give to active Music Presence listeners that have the specified roles",
)
async def set_role(
    interaction: discord.Interaction,
    for_role: Optional[discord.Role],
    listener_role: Optional[discord.Role],
    summary: Optional[bool],
):
    if interaction.guild_id is None:
        return await interaction.response.send_message("No guild ID for interaction")
    if not for_role and listener_role:
        return await interaction.response.send_message(
            "Need a role to set the listener role for"
        )
    is_reset = not listener_role
    guild_id = str(interaction.guild.id)
    if not settings.dexists("roles", guild_id):
        if is_reset:
            return await interaction.response.send_message(
                "No listener roles configured"
            )
        settings.dadd("roles", (guild_id, {}))
    if is_reset and not for_role:
        await remove_all_listener_roles_from_all(interaction.guild)
        settings.dpop("roles", str(interaction.guild.id))
        return await interaction.response.send_message(
            "Removed all listener roles from all members"
        )
    guild_roles = settings.dget("roles", guild_id)
    if is_reset and for_role:
        if str(for_role.id) in guild_roles:
            listener_role = await remove_listener_role_from_all(
                interaction.guild, for_role
            )
            listener_role_id = guild_roles[str(for_role.id)]
            assert listener_role is not None and listener_role.id == listener_role_id
            del guild_roles[str(for_role.id)]
            settings.dadd("roles", (guild_id, guild_roles))
            return await interaction.response.send_message(
                f"Disabled monitoring for <@&{for_role.id}> "
                f"and removed the <@&{listener_role_id}> role from all members",
                allowed_mentions=discord.AllowedMentions(roles=False),
            )
        else:
            return await interaction.response.send_message(
                f"No listener role configured for role <@&{for_role.id}>",
                allowed_mentions=discord.AllowedMentions(roles=False),
            )
    if str(listener_role.id) in guild_roles:
        return await interaction.response.send_message(
            f"Cannot use <@&{listener_role.id}> as a listener role. "
            "It is already used as a requirement for a listener role",
            allowed_mentions=discord.AllowedMentions(roles=False),
        )
    for other_listener_role_id in guild_roles.values():
        if for_role.id == other_listener_role_id:
            return await interaction.response.send_message(
                f"Cannot use <@&{for_role.id}> as a requirement for a listener role. "
                "It is already used as a listener role",
                allowed_mentions=discord.AllowedMentions(roles=False),
            )
    if not listener_role.is_assignable():
        return await interaction.response.send_message(
            "Cannot assign this role to server members. "
            "Make sure the bot's role is above the specified role"
        )
    if not listener_role.permissions.is_subset(discord.Permissions.none()):
        return await interaction.response.send_message(
            "Only roles without any extra permissions are allowed"
        )
    guild_roles[str(for_role.id)] = listener_role.id
    settings.dadd("roles", (guild_id, guild_roles))
    await check_guild(interaction.guild)
    await interaction.response.send_message(
        f"Listener role for <@&{for_role.id}> is now <@&{listener_role.id}>"
        + (f"\n{get_role_overview(interaction.guild)}" if summary else ""),
        allowed_mentions=discord.AllowedMentions(roles=False),
    )


@tree.command(
    name="roles",
    description="List all listener roles and their respective parent roles",
)
async def list_roles(interaction: discord.Interaction):
    if not settings.dexists("roles", str(interaction.guild.id)):
        return await interaction.response.send_message(
            "No listener roles configured for this server"
        )
    overview = get_role_overview(interaction.guild)
    await interaction.response.send_message(
        overview,
        allowed_mentions=discord.AllowedMentions(roles=False),
    )


@tree.command(
    name="joined",
    description="Check the join time of yourself or another user with some extras",
)
@discord.app_commands.describe(
    member="The member to check (leave empty to check yourself)"
)
async def member_number(
    interaction: discord.Interaction, member: discord.Member = None
):
    target_member = member or interaction.user
    guild = interaction.guild

    members_by_join_date = sorted(
        [member for member in guild.members if not member.bot],
        key=lambda m: m.joined_at or discord.utils.utcnow(),
    )

    try:
        member_index = members_by_join_date.index(target_member)
    except ValueError:
        await interaction.response.send_message(
            f"❌ Could not find {'yourself' if member is None else target_member.display_name} in the member list.",
            ephemeral=True,
        )
        return

    member_number = member_index + 1
    total_members = len(members_by_join_date)
    join_date = (
        target_member.joined_at.strftime("%B %d, %Y")
        if target_member.joined_at
        else "Unknown"
    )

    embed = discord.Embed(
        title="Member Timeline Position", color=0xE6DFD0  # Presence Beige:tm:
    )

    if member:
        embed.description = (
            f"**{target_member.display_name}** joined this server on **{join_date}**"
        )
        embed.add_field(
            name="Member Number",
            value=f"#{member_number} out of {total_members}",
            inline=False,
        )
    else:
        embed.description = f"You joined this server on **{join_date}**"
        embed.add_field(
            name="Your Member Number",
            value=f"#{member_number} out of {total_members}",
            inline=False,
        )

    if target_member.display_avatar:
        embed.set_thumbnail(url=target_member.display_avatar.url)

    percentage = (
        round(((total_members - member_number) / (total_members - 1)) * 100, 1)
        if total_members > 1
        else 0
    )
    embed.add_field(
        name="Early Bird Percentage",
        value=f"You joined earlier than {percentage}% of members",
        inline=True,
    )

    embed.set_footer(
        text=f"{guild.name} • Server created on {guild.created_at.strftime('%B %d, %Y')}"
    )

    await interaction.response.send_message(embed=embed)


# Annoyingly required catch for when member cannot be found by Discord else we get interaction timeout & ugly error
@tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: discord.app_commands.AppCommandError
):
    if interaction.command and interaction.command.name == "membernum":
        if isinstance(error, discord.app_commands.errors.TransformerError):
            await interaction.response.send_message(
                "❌ Could not find that member in the server.", ephemeral=True
            )


@tree.command(
    name="listening",
    description="Register your currently active listening status for the listener role",
)
async def listening_role(interaction: discord.Interaction, delete: Optional[bool]):
    guild_member = None
    for member in interaction.guild.members:
        if member.id == interaction.user.id:
            guild_member = member
            break
    if guild_member is None:
        return await interaction.response.send_message(
            "Interaction user not found amongst guild members"
        )
    user_id = guild_member.id
    if delete:
        settings.dpop("user_apps", str(user_id))
        await check_member(guild_member)
        await interaction.response.send_message(
            f"Removed any registered app IDs for <@{user_id}>"
        )
        return
    count = 0
    for activity in guild_member.activities:
        if (
            not isinstance(activity, discord.Spotify)
            and isinstance(activity, discord.Activity)
            and activity.type == discord.ActivityType.listening
        ):
            app_id = activity.application_id
            if app_id is None:
                continue
            if settings.dexists("apps", str(app_id)):
                await interaction.response.send_message(
                    f"App ID `{app_id}` is already known"
                )
                return
            if not settings.dexists("user_apps", str(user_id)):
                settings.dadd("user_apps", (str(user_id), {}))
            user_apps = settings.dget("user_apps", str(user_id))
            # Only one custom app ID is allowed per user.
            user_apps.clear()
            user_apps[str(app_id)] = dataclasses.asdict(
                UserApp(
                    app_id=app_id,
                    user_id=guild_member.id,
                    timestamp=int(time()),
                )
            )
            settings.dadd("user_apps", (str(user_id), user_apps))
            await interaction.response.send_message(
                f"Registered listening role for app ID `{app_id}` for <@{user_id}>"
            )
            count += 1
    if count == 0:
        return await interaction.response.send_message(
            f"No app ID found, make sure your presence is visible"
        )
    await check_member(guild_member)


@tree.command(
    name="stop",
    description="Stop the bot and remove the listener role from all members in all servers",
)
async def stop(interaction: discord.Interaction):
    for guild in client.guilds:
        await remove_all_listener_roles_from_all(guild)
    await interaction.response.send_message("Removed all roles, stopping now")
    await client.close()


class Platform(str, Enum):
    WIN = "Windows"
    MAC = "Mac"


PLATFORM_LOG_FILES = {
    Platform.WIN: "%APPDATA%\\Music Presence\\presence.log",
    Platform.MAC: "~/Library/Application Support/Music Presence/presence.log",
}


async def logs_response(
    interaction: discord.Interaction, platform: discord.app_commands.Choice[str] = None
):
    lines = ["You can find the log file for Music Presence here:"]
    for platform in Platform:
        if platform is None or platform == platform.value:
            filepath = PLATFORM_LOG_FILES[platform]
            lines.append(f"- {platform.value}: `{filepath}`")
    await interaction.response.send_message("\n".join(lines))


@tree.command(
    name="logs",
    description="Tells you where the Music Presence logs are located",
)
@discord.app_commands.choices(
    os=[
        discord.app_commands.Choice(name="Windows", value=Platform.WIN),
        discord.app_commands.Choice(name="Mac", value=Platform.MAC),
    ]
)
async def logs(
    interaction: discord.Interaction,
    os: discord.app_commands.Choice[str] = None,
):
    await logs_response(interaction, os)


LATEST_RELEASE_URL = (
    "https://api.github.com/repos/ungive/discord-music-presence/releases?per_page=1"
)


@memoize(
    configuration=DefaultInMemoryCacheConfiguration(
        update_after=timedelta(minutes=15), expire_after=timedelta(minutes=30)
    )
)
async def latest_github_release_version() -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(LATEST_RELEASE_URL) as response:
                if response.status != 200:
                    raise RuntimeError(
                        "Failed to get latest version from the GitHub API"
                    )
                data = json.loads(await response.read())
                if len(data) < 0:
                    raise RuntimeError("The GitHub API returned an empty result")
                latest_release = data[0]
                tag = latest_release["tag_name"]
                if not re.search(r"^v\d+\.\d+\.\d+$", tag):
                    raise RuntimeError(f"Bad version tag format: {tag}")
                return tag[1:]
    except Exception as e:
        print(f"GitHub API request failed: {e}")
        traceback.print_exc()


class HelpTopic(str, Enum):
    Installation = "Installation"
    PlayerDetection = "Player detection"
    ApplicationLogs = "Application logs"


HELP_URL_INSTALL = "https://github.com/ungive/discord-music-presence/blob/master/documentation/installation-instructions.md"
HELP_URL_TROUBLESHOOTING = "https://github.com/ungive/discord-music-presence/blob/master/documentation/troubleshooting.md"
HELP_MESSAGE_LINES = {
    None: [
        f"Choose the topic you need help with:",
        f"- **{HelpTopic.Installation.value}**: For detailed installation instructions read the steps outlined [**here**](<{HELP_URL_INSTALL}>). "
        f"If you can't find the download links for Music Presence, use the `/help topic:{HelpTopic.Installation.name}` command",
        f"- **{HelpTopic.PlayerDetection.value}**: For troubleshooting undetected media players find help [**here**](<{HELP_URL_TROUBLESHOOTING}>)",
        f"- **{HelpTopic.ApplicationLogs.value}**: For paths to log files use the `/{logs.name}` command",
    ],
    HelpTopic.Installation: [
        f"- To download the app, click any of the buttons below",
        f"- Read the installation instructions [**here**](<{HELP_URL_INSTALL}>) "
        f"if you need help with installing Music Presence",
    ],
    HelpTopic.PlayerDetection: [
        f"- For troubleshooting undetected media players find help [**here**](<{HELP_URL_TROUBLESHOOTING}>)",
        f"- Note that your media player might need a plugin to work with Music Presence. "
        f"You'll find more information at the provided help page",
    ],
}
HELP_DOWNLOAD_URLS_FORMAT = [
    (
        "Windows",
        "https://github.com/ungive/discord-music-presence/releases/download/v{version}/musicpresence-{version}-windows-x64-installer.exe",
    ),
    (
        "Mac Apple Silicon",
        "https://github.com/ungive/discord-music-presence/releases/download/v{version}/musicpresence-{version}-mac-arm64.dmg",
    ),
    (
        "Mac Intel",
        "https://github.com/ungive/discord-music-presence/releases/download/v{version}/musicpresence-{version}-mac-x86_64.dmg",
    ),
    (
        "All downloads",
        "https://github.com/ungive/discord-music-presence/releases/latest",
    ),
]
HELP_TROUBLESHOOTING_URLS = [("Troubleshooting", HELP_URL_TROUBLESHOOTING)]


async def get_download_urls() -> list[tuple[str, str]]:
    version = await latest_github_release_version()
    return [
        (name, url.format(version=version)) for name, url in HELP_DOWNLOAD_URLS_FORMAT
    ]


def get_help_message(topic: Optional[HelpTopic]):
    if topic in HELP_MESSAGE_LINES:
        return "\n".join(HELP_MESSAGE_LINES[topic])
    return "No help message for this topic available"


class LinkButtons(discord.ui.View):
    def __init__(self, labelled_urls: list[tuple[str, str]]):
        super().__init__()
        for name, url in labelled_urls:
            self.add_item(discord.ui.Button(label=name, url=url))


@tree.command(
    name="help",
    description="Use this command if you need help with Music Presence",
)
@discord.app_commands.choices(
    topic=[
        discord.app_commands.Choice(
            name=HelpTopic.Installation.value,
            value=HelpTopic.Installation.value,
        ),
        discord.app_commands.Choice(
            name=HelpTopic.PlayerDetection.value,
            value=HelpTopic.PlayerDetection.value,
        ),
        discord.app_commands.Choice(
            name=HelpTopic.ApplicationLogs.value,
            value=HelpTopic.ApplicationLogs.value,
        ),
    ]
)
async def help(
    interaction: discord.Interaction,
    topic: discord.app_commands.Choice[str] = None,
):
    value = HelpTopic(topic.value) if topic is not None else None
    view = discord.utils.MISSING
    if value == HelpTopic.Installation:
        try:
            view = LinkButtons(await get_download_urls())
        except Exception as e:
            return await interaction.response.send_message(f"An error occurred: {e}")
    elif value == HelpTopic.PlayerDetection:
        view = LinkButtons(HELP_TROUBLESHOOTING_URLS)
    elif value == HelpTopic.ApplicationLogs:
        return await logs_response(interaction)
    await interaction.response.send_message(get_help_message(value), view=view)


# TODO properly remove roles from users when the bot is shut down

client.run(os.getenv("BOT_TOKEN"))
