import discord
from discord.ext import commands
import asyncio
import logging

logger = logging.getLogger("bot")

class AutoRoleCog(commands.Cog):
    def __init__(self, bot, role_id: int):
        self.bot = bot
        self.role_id = role_id

    @commands.Cog.listener()
    async def on_ready(self):
        # Wait until bot is ready and guilds are loaded
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            role = guild.get_role(self.role_id)
            if not role:
                logger.error(f"Role ID {self.role_id} not found in guild {guild.name} ({guild.id})")
                continue

            # Apply role to all members who don't have it yet
            for member in guild.members:
                if role not in member.roles and not member.bot:  # skip bots if you want
                    try:
                        await member.add_roles(role, reason="Auto-role on bot startup")
                        logger.info(f"Added role {role.name} to {member.display_name} on startup")
                        # Avoid hitting rate limits
                        await asyncio.sleep(1)
                    except discord.Forbidden:
                        logger.error(f"Missing permissions to add role to {member.display_name}")
                    except Exception as e:
                        logger.error(f"Error adding role to {member.display_name}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        role = member.guild.get_role(self.role_id)
        if role:
            try:
                await member.add_roles(role, reason="Auto-role on member join")
                logger.info(f"Added role {role.name} to {member.display_name} on join")
            except discord.Forbidden:
                logger.error(f"Missing permissions to add role to {member.display_name}")
            except Exception as e:
                logger.error(f"Error adding role to {member.display_name}: {e}")

async def setup(bot):
    # Replace ROLE_ID with your target role ID
    ROLE_ID = 1389711048461910057
    await bot.add_cog(AutoRoleCog(bot, ROLE_ID))
