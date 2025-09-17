# cogs/admin.py - Updated for multi-server support
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import traceback
from typing import Optional

from utils.config import (
    get_channel_id,
    get_all_server_configs,
    is_server_configured
)

# FIX: Import get_logger from the updated logger module
from utils.logger import get_logger


class DevToolsCog(commands.Cog):
    """Simple developer tools for bot management"""

    def __init__(self, bot):
        self.bot = bot
        # FIX: The logger is now a global singleton, so we just get it by name.
        self.logger = get_logger("관리자 도구")
        self.reload_stats = {
            'total_reloads': 0,
            'successful_reloads': 0,
            'failed_reloads': 0,
            'last_reload_time': None
        }
        self.logger.info("관리자 도구 코그가 초기화되었습니다.")

    async def cog_check(self, ctx):
        """Only allow bot owner to use these commands"""
        return await self.bot.is_owner(ctx.author)

    # =============================================================================
    # SLASH COMMANDS FOR COG MANAGEMENT
    # =============================================================================

    @app_commands.command(name="reload", description="Reload a specific cog")
    @app_commands.describe(cog="Name of the cog to reload (e.g., casino_slots)")
    async def reload_cog(self, interaction: discord.Interaction, cog: str):
        """Reload a specific cog"""
        guild_id = interaction.guild.id if interaction.guild else None

        try:
            await self.bot.reload_extension(f'cogs.{cog}')

            embed = discord.Embed(
                title="✅ Cog Reloaded Successfully",
                description=f"Successfully reloaded `{cog}`",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )

            embed.add_field(
                name="📊 Stats",
                value=f"Total Reloads: {self.reload_stats['total_reloads'] + 1}",
                inline=True
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.info(f"Cog '{cog}' reloaded successfully.", extra={'guild_id': guild_id})

            self.reload_stats['successful_reloads'] += 1
            self.reload_stats['total_reloads'] += 1
            self.reload_stats['last_reload_time'] = discord.utils.utcnow()

        except commands.ExtensionNotLoaded:
            embed = discord.Embed(
                title="❌ Cog Not Loaded",
                description=f"Cog `{cog}` is not currently loaded.\nUse `/load {cog}` to load it first.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.warning(f"Failed to reload '{cog}' - not loaded.", extra={'guild_id': guild_id})

        except commands.ExtensionNotFound:
            embed = discord.Embed(
                title="❌ Cog Not Found",
                description=f"Cog `{cog}` does not exist.\nCheck if the file `cogs/{cog}.py` exists.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.warning(f"Failed to reload '{cog}' - not found.", extra={'guild_id': guild_id})

        except Exception as e:
            error_msg = str(e)
            if len(error_msg) > 1000:
                error_msg = error_msg[:1000] + "..."

            embed = discord.Embed(
                title="❌ Reload Failed",
                description=f"Failed to reload `{cog}`:",
                color=discord.Color.red()
            )
            embed.add_field(name="Error Details", value=f"```py\n{error_msg}\n```", inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.error(f"Failed to reload '{cog}'.", exc_info=True, extra={'guild_id': guild_id})

            self.reload_stats['failed_reloads'] += 1
            self.reload_stats['total_reloads'] += 1

    @app_commands.command(name="load", description="Load a new cog")
    @app_commands.describe(cog="Name of the cog to load")
    async def load_cog(self, interaction: discord.Interaction, cog: str):
        """Load a new cog"""
        guild_id = interaction.guild.id if interaction.guild else None

        try:
            await self.bot.load_extension(f'cogs.{cog}')

            embed = discord.Embed(
                title="✅ Cog Loaded Successfully",
                description=f"Successfully loaded `{cog}`",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.info(f"Cog '{cog}' loaded successfully.", extra={'guild_id': guild_id})

        except commands.ExtensionAlreadyLoaded:
            embed = discord.Embed(
                title="❌ Cog Already Loaded",
                description=f"Cog `{cog}` is already loaded.\nUse `/reload {cog}` to reload it.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.warning(f"Failed to load '{cog}' - already loaded.", extra={'guild_id': guild_id})

        except commands.ExtensionNotFound:
            embed = discord.Embed(
                title="❌ Cog Not Found",
                description=f"Cog `{cog}` does not exist.\nCheck if the file `cogs/{cog}.py` exists.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.warning(f"Failed to load '{cog}' - not found.", extra={'guild_id': guild_id})

        except Exception as e:
            embed = discord.Embed(
                title="❌ Load Failed",
                description=f"Failed to load `{cog}`:\n```py\n{str(e)}\n```",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.error(f"Failed to load '{cog}'.", exc_info=True, extra={'guild_id': guild_id})

    @app_commands.command(name="unload", description="Unload a cog")
    @app_commands.describe(cog="Name of the cog to unload")
    async def unload_cog(self, interaction: discord.Interaction, cog: str):
        """Unload a cog"""
        guild_id = interaction.guild.id if interaction.guild else None

        if cog.lower() in ['admin', 'dev_tools']:
            embed = discord.Embed(
                title="❌ Cannot Unload",
                description="Cannot unload the admin/dev_tools cog (you'd lose access to these commands!)",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.warning(f"Attempted to unload core admin cog '{cog}'.", extra={'guild_id': guild_id})
            return

        try:
            await self.bot.unload_extension(f'cogs.{cog}')

            embed = discord.Embed(
                title="✅ Cog Unloaded Successfully",
                description=f"Successfully unloaded `{cog}`",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.info(f"Cog '{cog}' unloaded successfully.", extra={'guild_id': guild_id})

        except commands.ExtensionNotLoaded:
            embed = discord.Embed(
                title="❌ Cog Not Loaded",
                description=f"Cog `{cog}` is not currently loaded.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.warning(f"Failed to unload '{cog}' - not loaded.", extra={'guild_id': guild_id})

        except Exception as e:
            embed = discord.Embed(
                title="❌ Unload Failed",
                description=f"Failed to unload `{cog}`:\n```py\n{str(e)}\n```",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.error(f"Failed to unload '{cog}'.", exc_info=True, extra={'guild_id': guild_id})

    @app_commands.command(name="listcogs", description="List all loaded cogs")
    async def list_cogs(self, interaction: discord.Interaction):
        """List all currently loaded cogs"""
        guild_id = interaction.guild.id if interaction.guild else None
        loaded_cogs = list(self.bot.extensions.keys())

        if not loaded_cogs:
            embed = discord.Embed(
                title="🔧 No Cogs Loaded",
                description="No cogs are currently loaded.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            self.logger.info("Listed loaded cogs: None loaded.", extra={'guild_id': guild_id})
            return

        embed = discord.Embed(
            title="🔧 Loaded Cogs Status",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )

        # Group cogs by category
        casino_cogs = []
        utility_cogs = []
        other_cogs = []

        for cog_path in loaded_cogs:
            cog_name = cog_path.replace('cogs.', '')

            if any(word in cog_name.lower() for word in ['casino', 'slot', 'coin', 'game']):
                casino_cogs.append(cog_name)
            elif any(word in cog_name.lower() for word in ['admin', 'dev', 'util', 'log', 'setup']):
                utility_cogs.append(cog_name)
            else:
                other_cogs.append(cog_name)

        if casino_cogs:
            casino_text = "\n".join([f"🎰 `{cog}`" for cog in sorted(casino_cogs)])
            embed.add_field(name="🎮 Casino & Games", value=casino_text, inline=False)

        if utility_cogs:
            util_text = "\n".join([f"🔧 `{cog}`" for cog in sorted(utility_cogs)])
            embed.add_field(name="🛠️ Utilities & Admin", value=util_text, inline=False)

        if other_cogs:
            other_text = "\n".join([f"📦 `{cog}`" for cog in sorted(other_cogs)])
            embed.add_field(name="📋 Other Cogs", value=other_text, inline=False)

        embed.add_field(
            name="📊 Summary",
            value=f"**Total Loaded:** {len(loaded_cogs)} cogs",
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.logger.info(f"Listed loaded cogs: {len(loaded_cogs)} loaded.", extra={'guild_id': guild_id})

    @app_commands.command(name="serverstatus", description="Show multi-server configuration status")
    async def server_status(self, interaction: discord.Interaction):
        """Show status of all configured servers"""
        guild_id = interaction.guild.id if interaction.guild else None
        all_configs = get_all_server_configs()

        # ... (rest of the command logic is unchanged)

        if not all_configs:
            embed = discord.Embed(
                title="📊 Multi-Server Status",
                description="No servers are currently configured.",
                color=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title="📊 Multi-Server Status",
                description=f"Bot is serving **{len(self.bot.guilds)}** servers, **{len(all_configs)}** configured",
                color=discord.Color.blue()
            )

            # Show configured servers
            server_list = []
            for guild_id_str, config in list(all_configs.items())[:10]:  # Show first 10
                guild = self.bot.get_guild(int(guild_id_str))
                guild_name = guild.name if guild else config.get('guild_name', 'Unknown Server')

                enabled_features = sum(config.get('features', {}).values())
                configured_channels = len([c for c in config.get('channels', {}).values() if c])

                status = "🟢 Online" if guild else "🔴 Offline"
                server_list.append(
                    f"{status} **{guild_name}**\n└ Features: {enabled_features}, Channels: {configured_channels}")

            if server_list:
                embed.add_field(
                    name="🔗 Configured Servers",
                    value="\n\n".join(server_list),
                    inline=False
                )

                if len(all_configs) > 10:
                    embed.add_field(
                        name="📋 Additional",
                        value=f"...and {len(all_configs) - 10} more configured servers",
                        inline=False
                    )

            # Show current server status
            if interaction.guild:
                current_config = all_configs.get(str(interaction.guild.id))
                if current_config:
                    enabled_features = list(current_config.get('features', {}).keys())
                    enabled_count = sum(current_config.get('features', {}).values())
                    embed.add_field(
                        name=f"⚙️ Current Server ({interaction.guild.name})",
                        value=f"Status: ✅ Configured\nEnabled Features: {enabled_count}\nFeatures: {', '.join(enabled_features) if enabled_features else 'None'}",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name=f"⚙️ Current Server ({interaction.guild.name})",
                        value="Status: ❌ Not Configured\nRun `/봇셋업` to configure this server",
                        inline=False
                    )

        await interaction.response.send_message(embed=embed, ephemeral=True)
        self.logger.info("Displayed server status.", extra={'guild_id': guild_id})

    @app_commands.command(name="reloadall", description="Reload all loaded cogs")
    async def reload_all_cogs(self, interaction: discord.Interaction):
        """Reload all currently loaded cogs"""
        guild_id = interaction.guild.id if interaction.guild else None
        await interaction.response.defer(ephemeral=True)

        loaded_cogs = list(self.bot.extensions.keys())
        results = {"success": [], "failed": []}

        for cog in loaded_cogs:
            try:
                await self.bot.reload_extension(cog)
                results["success"].append(cog.replace('cogs.', ''))
                self.reload_stats['successful_reloads'] += 1
            except Exception as e:
                # FIX: Use structured logging with `extra` for multi-server context
                self.logger.error(f"Failed to reload cog '{cog}'.", exc_info=True, extra={'guild_id': guild_id})
                results["failed"].append((cog.replace('cogs.', ''), str(e)[:100]))
                self.reload_stats['failed_reloads'] += 1

            self.reload_stats['total_reloads'] += 1

        self.reload_stats['last_reload_time'] = discord.utils.utcnow()
        self.logger.info(
            f"Reloaded all cogs. Success: {len(results['success'])}, Failed: {len(results['failed'])}",
            extra={'guild_id': guild_id}
        )

        # Create result embed
        if results["success"] and not results["failed"]:
            color = discord.Color.green()
            title = "✅ All Cogs Reloaded Successfully"
        elif results["success"] and results["failed"]:
            color = discord.Color.orange()
            title = "⚠️ Partial Reload Success"
        else:
            color = discord.Color.red()
            title = "❌ Reload Failed"

        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())

        if results["success"]:
            success_text = "\n".join([f"✅ `{cog}`" for cog in results["success"][:10]])
            if len(results["success"]) > 10:
                success_text += f"\n... and {len(results['success']) - 10} more"
            embed.add_field(name="Successfully Reloaded", value=success_text, inline=False)

        if results["failed"]:
            failed_text = "\n".join([f"❌ `{cog}`: {error}" for cog, error in results["failed"][:5]])
            if len(results["failed"]) > 5:
                failed_text += f"\n... and {len(results['failed']) - 5} more failures"
            embed.add_field(name="Failed to Reload", value=failed_text, inline=False)

        embed.add_field(
            name="📊 Results",
            value=f"**Success:** {len(results['success'])}\n**Failed:** {len(results['failed'])}\n**Total:** {len(loaded_cogs)}",
            inline=True
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="devstats", description="Show development statistics")
    async def dev_stats(self, interaction: discord.Interaction):
        """Show development statistics"""
        guild_id = interaction.guild.id if interaction.guild else None
        self.logger.info("Displayed developer stats.", extra={'guild_id': guild_id})

        embed = discord.Embed(
            title="📊 Development Statistics",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )

        # Reload stats
        success_rate = 0
        if self.reload_stats['total_reloads'] > 0:
            success_rate = (self.reload_stats['successful_reloads'] / self.reload_stats['total_reloads'] * 100)

        embed.add_field(
            name="🔄 Reload Statistics",
            value=f"**Total Reloads:** {self.reload_stats['total_reloads']}\n"
                  f"**Successful:** {self.reload_stats['successful_reloads']}\n"
                  f"**Failed:** {self.reload_stats['failed_reloads']}\n"
                  f"**Success Rate:** {success_rate:.1f}%",
            inline=True
        )

        # Multi-server stats
        all_configs = get_all_server_configs()
        configured_servers = len(all_configs)
        total_servers = len(self.bot.guilds)

        embed.add_field(
            name="🌐 Multi-Server Stats",
            value=f"**Total Servers:** {total_servers}\n"
                  f"**Configured:** {configured_servers}\n"
                  f"**Configuration Rate:** {(configured_servers / total_servers * 100):.1f}%" if total_servers > 0 else "0%",
            inline=True
        )

        # System info
        embed.add_field(
            name="🤖 Bot Status",
            value=f"**Loaded Cogs:** {len(self.bot.extensions)}\n"
                  f"**Total Users:** {len(self.bot.users)}\n"
                  f"**Latency:** {self.bot.latency * 1000:.1f}ms",
            inline=True
        )

        # Feature usage stats
        if all_configs:
            feature_stats = {}
            for config in all_configs.values():
                for feature, enabled in config.get('features', {}).items():
                    if enabled:
                        feature_stats[feature] = feature_stats.get(feature, 0) + 1

            if feature_stats:
                top_features = sorted(feature_stats.items(), key=lambda x: x[1], reverse=True)[:5]
                feature_text = "\n".join([f"• {feature}: {count} servers" for feature, count in top_features])
                embed.add_field(name="🚀 Most Used Features", value=feature_text, inline=False)

        # Last reload info
        if self.reload_stats['last_reload_time']:
            embed.add_field(
                name="⏰ Last Activity",
                value=f"**Last Reload:** {discord.utils.format_dt(self.reload_stats['last_reload_time'], 'R')}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="sync", description="Sync slash commands")
    @app_commands.describe(guild_only="Sync only to this guild (faster) or globally")
    async def sync_commands(self, interaction: discord.Interaction, guild_only: bool = True):
        """Handle syncing application commands"""
        guild_id = interaction.guild.id if interaction.guild else None
        await interaction.response.defer(ephemeral=True)

        try:
            if guild_only and interaction.guild:
                synced = await self.bot.tree.sync(guild=interaction.guild)
                embed = discord.Embed(
                    title="✅ Commands Synced (Guild)",
                    description=f"Synced {len(synced)} commands to this guild.",
                    color=discord.Color.green()
                )
                self.logger.info(f"Synced {len(synced)} commands to guild.", extra={'guild_id': guild_id})
            else:
                synced = await self.bot.tree.sync()
                embed = discord.Embed(
                    title="✅ Commands Synced (Global)",
                    description=f"Synced {len(synced)} commands globally.\nMay take up to 1 hour to appear everywhere.",
                    color=discord.Color.green()
                )
                self.logger.info(f"Synced {len(synced)} commands globally.", extra={'guild_id': guild_id})

            embed.timestamp = discord.utils.utcnow()

        except Exception as e:
            embed = discord.Embed(
                title="❌ Sync Failed",
                description=f"Failed to sync commands:\n```py\n{str(e)}\n```",
                color=discord.Color.red()
            )
            self.logger.error("Failed to sync commands.", exc_info=True, extra={'guild_id': guild_id})

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =============================================================================
    # TEXT COMMANDS (QUICK ACCESS)
    # =============================================================================

    @commands.command(name='r', aliases=['reload'])
    @commands.is_owner()
    async def reload_text(self, ctx, *, cog: str):
        """Quick reload command (text version)"""
        guild_id = ctx.guild.id if ctx.guild else None
        try:
            await self.bot.reload_extension(f'cogs.{cog}')
            await ctx.message.add_reaction('✅')
            self.reload_stats['successful_reloads'] += 1
            self.logger.info(f"Cog '{cog}' reloaded successfully via text command.", extra={'guild_id': guild_id})
        except Exception as e:
            # FIX: Use structured logging with `extra`
            self.logger.error(f"Failed to reload cog '{cog}' via text command.", exc_info=True,
                              extra={'guild_id': guild_id})
            await ctx.send(f"❌ **Reload Failed:** `{cog}`\n```py\n{str(e)}\n```")
            self.reload_stats['failed_reloads'] += 1

        self.reload_stats['total_reloads'] += 1
        self.reload_stats['last_reload_time'] = discord.utils.utcnow()

    @commands.command(name='ra', aliases=['reloadall'])
    @commands.is_owner()
    async def reload_all_text(self, ctx):
        """Quick reload all command (text version)"""
        guild_id = ctx.guild.id if ctx.guild else None
        loaded_cogs = list(self.bot.extensions.keys())
        success_count = 0

        for cog in loaded_cogs:
            try:
                await self.bot.reload_extension(cog)
                success_count += 1
                self.reload_stats['successful_reloads'] += 1
            except Exception:
                self.reload_stats['failed_reloads'] += 1
                self.logger.error(f"Failed to reload cog '{cog}' during reload all.", exc_info=True,
                                  extra={'guild_id': guild_id})

            self.reload_stats['total_reloads'] += 1

        self.reload_stats['last_reload_time'] = discord.utils.utcnow()
        self.logger.info(
            f"Reloaded all cogs via text command. Success: {success_count}, Failed: {len(loaded_cogs) - success_count}",
            extra={'guild_id': guild_id}
        )
        await ctx.send(f"🔄 Reloaded {success_count}/{len(loaded_cogs)} cogs")

    @commands.command(name='lc', aliases=['listcogs'])
    @commands.is_owner()
    async def list_cogs_text(self, ctx):
        """Quick list cogs command (text version)"""
        guild_id = ctx.guild.id if ctx.guild else None
        loaded_cogs = [cog.replace('cogs.', '') for cog in self.bot.extensions.keys()]
        if loaded_cogs:
            cog_list = ', '.join([f"`{cog}`" for cog in sorted(loaded_cogs)])
            await ctx.send(f"**Loaded Cogs ({len(loaded_cogs)}):** {cog_list}")
            self.logger.info(f"Listed loaded cogs via text command: {len(loaded_cogs)} loaded.",
                             extra={'guild_id': guild_id})
        else:
            await ctx.send("No cogs are currently loaded.")
            self.logger.info("Listed loaded cogs: None loaded.", extra={'guild_id': guild_id})


async def setup(bot):
    await bot.add_cog(DevToolsCog(bot))