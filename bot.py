# Required permissions:
# - Manage Roles (required to set and remove roles from members)
# - Use Slash Commands (required to create and register commands in guilds)
# Invite link:
# https://discord.com/api/oauth2/authorize?client_id=1236022326773022800&permissions=2415919104&scope=bot%20applications.commands

MUSIC_APP_ID = 1205619376275980288
PODCAST_APP_ID = 1292142821482172506
PLAYERS_JSON_URL = "https://live.musicpresence.app/v2/players.min.json"

import os
import json
import asyncio
import aiohttp
from typing import Optional
from collections import defaultdict

import discord
import pickledb
from dotenv import load_dotenv


def load_settings(version: int = 0) -> pickledb.PickleDB:
    settings = pickledb.load(f"settings.{version}.db", True)
    for key in ["apps", "roles"]:
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
            if listener_role in member.roles:
                await member.remove_roles(listener_role)
    return listener_role


async def remove_all_listener_roles_from_all(guild: discord.Guild):
    for listener_role in get_listener_roles(guild):
        for member in guild.members:
            if listener_role in member.roles:
                await member.remove_roles(listener_role)


async def check_member(member: discord.Member):
    if member.status in (discord.Status.invisible, discord.Status.offline):
        return await remove_all_listener_roles_from_member(member)
    apps = settings.get("apps")
    for activity in member.activities:
        if (
            not isinstance(activity, discord.Spotify)
            and activity.type == discord.ActivityType.listening
            and str(activity.application_id) in apps
        ):
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
    await tree.sync(guild=guild)


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
                app_id = player["discord_application_id"]
                result[str(app_id)] = True
    settings.set("apps", result)
    print(f"Updated application IDs ({len(result)} entries)")


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
            f"<@&{listener_role_id}> is assigned to {rreplace(', '.join([
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
    description="list all listener roles and their respective parent roles",
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
    name="stop",
    description="Stop the bot and remove the listener role from all members in all servers",
)
async def stop(interaction: discord.Interaction):
    for guild in client.guilds:
        await remove_all_listener_roles_from_all(guild)
    await interaction.response.send_message("Removed all roles, stopping now")
    await client.close()


# TODO properly remove roles from users when the bot is shut down

client.run(os.getenv("BOT_TOKEN"))
