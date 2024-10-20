# Required permissions:
# - Manage Roles (required to set and remove roles from members)
# - Use Slash Commands (required to create and register commands in guilds)
# Invite link:
# https://discord.com/api/oauth2/authorize?client_id=1236022326773022800&permissions=2415919104&scope=bot%20applications.commands

PLAYERS_JSON_URL = "https://live.musicpresence.app/v2/players.min.json"

import os
import json
import asyncio
import aiohttp
from typing import Optional

import discord
import pickledb
from dotenv import load_dotenv


def load_settings(version: int = 0) -> pickledb.PickleDB:
    settings = pickledb.load(f"settings.{version}.db", True)
    if not settings.exists("apps"):
        settings.dcreate("apps")  # allowed discord app ids
    if not settings.exists("roles"):
        settings.dcreate("roles")  # listener roles per guild
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


def get_role_for_guild(guild: discord.Guild) -> discord.Role | None:
    if settings.dexists("roles", str(guild.id)):
        role = guild.get_role(settings.dget("roles", str(guild.id)))
        if role is None:
            # the role seems to have been deleted
            settings.dpop("roles", str(guild.id))
        return role
    return None


async def give_role(member: discord.Member):
    role = get_role_for_guild(member.guild)
    if role is not None and not role in member.roles:
        await member.add_roles(role)


async def remove_role(member: discord.Member):
    role = get_role_for_guild(member.guild)
    if role is not None and role in member.roles:
        await member.remove_roles(role)


async def remove_role_from_all(guild: discord.Guild):
    role = get_role_for_guild(guild)
    if role is not None:
        for member in guild.members:
            await member.remove_roles(role)


async def check_member(member: discord.Member):
    if member.status in (discord.Status.invisible, discord.Status.offline):
        return await remove_role(member)
    apps = settings.get("apps")
    for activity in member.activities:
        if (
            activity.type == discord.ActivityType.listening
            and str(activity.application_id) in apps
        ):
            return await give_role(member)
    await remove_role(member)


async def check_guild(guild: discord.Guild):
    idd = str(guild.id)
    if settings.dexists("roles", str(guild.id)):
        for member in guild.members:
            await check_member(member)


async def check_guilds():
    for guild in client.guilds:
        await setup_guild(guild)


async def setup_guild(guild: discord.Guild):
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)


async def update_apps():
    result = {}
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
    await check_guilds()
    print(f"Updated application IDs ({len(result)} entries)")


async def update_apps_periodically():
    while True:
        print("Updating application IDs")
        await update_apps()
        await asyncio.sleep(60 * 60)


@client.event
async def on_ready():
    client.loop.create_task(update_apps_periodically())


@client.event
async def on_guild_join(guild: discord.Guild):
    await setup_guild(guild)


@client.event
async def on_presence_update(_: discord.Member, member: discord.Member):
    await check_member(member)


@tree.command(
    name="role",
    description="Get or set the role to give to active Music Presence listeners",
)
async def set_role(interaction: discord.Interaction, role: Optional[discord.Role]):
    if interaction.guild_id is None:
        await interaction.response.send_message("No guild ID for interaction")
    if not role:
        if not settings.dexists("roles", str(interaction.guild_id)):
            await interaction.response.send_message("No listener role configured")
            return
        await remove_role_from_all(interaction.guild)
        role_id = settings.dget("roles", str(interaction.guild.id))
        settings.dpop("roles", str(interaction.guild.id))
        await interaction.response.send_message(
            f"Disabled monitoring and removed the <@&{role_id}> role from all members"
        )
        return
    if not role.is_assignable():
        return await interaction.response.send_message(
            "Cannot assign this role to members"
        )
    if not role.permissions.is_subset(discord.Permissions.none()):
        return await interaction.response.send_message(
            "Only roles without any extra permissions are allowed"
        )
    # NOTE pickledb inconsistency here:
    # integers are stored as strings but they remain integers in memory.
    settings.dadd("roles", (str(interaction.guild.id), role.id))
    await check_guild(interaction.guild)
    await interaction.response.send_message(
        f"Listener role is now <@&{role.id}>",
        allowed_mentions=discord.AllowedMentions(roles=False),
    )


@tree.command(
    name="stop",
    description="Stop the bot and remove the listener role from all members in all guilds",
)
async def stop(interaction: discord.Interaction):
    for guild in client.guilds:
        await remove_role_from_all(guild)
    await interaction.response.send_message("Removed all roles, stopping now")
    await client.close()


# TODO properly remove roles from users when the bot is shut down

client.run(os.getenv("BOT_TOKEN"))
