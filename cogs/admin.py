# cogs/admin.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import traceback
from typing import Optional


class DevToolsCog(commands.Cog):
    """Simple developer tools for bot management"""

    def __init__(self, bot):
        self.bot = bot
        self.reload_stats = {
            'total_reloads': 0,
            'successful_reloads': 0,
            'failed_reloads': 0,
            'last_reload_time': None
        }

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

        except commands.ExtensionNotFound:
            embed = discord.Embed(
                title="❌ Cog Not Found",
                description=f"Cog `{cog}` does not exist.\nCheck if the file `cogs/{cog}.py` exists.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

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
            self.reload_stats['failed_reloads'] += 1
            self.reload_stats['total_reloads'] += 1

    @app_commands.command(name="load", description="Load a new cog")
    @app_commands.describe(cog="Name of the cog to load")
    async def load_cog(self, interaction: discord.Interaction, cog: str):
        """Load a new cog"""
        try:
            await self.bot.load_extension(f'cogs.{cog}')

            embed = discord.Embed(
                title="✅ Cog Loaded Successfully",
                description=f"Successfully loaded `{cog}`",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except commands.ExtensionAlreadyLoaded:
            embed = discord.Embed(
                title="❌ Cog Already Loaded",
                description=f"Cog `{cog}` is already loaded.\nUse `/reload {cog}` to reload it.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except commands.ExtensionNotFound:
            embed = discord.Embed(
                title="❌ Cog Not Found",
                description=f"Cog `{cog}` does not exist.\nCheck if the file `cogs/{cog}.py` exists.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            embed = discord.Embed(
                title="❌ Load Failed",
                description=f"Failed to load `{cog}`:\n```py\n{str(e)}\n```",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="unload", description="Unload a cog")
    @app_commands.describe(cog="Name of the cog to unload")
    async def unload_cog(self, interaction: discord.Interaction, cog: str):
        """Unload a cog"""
        if cog.lower() in ['admin', 'dev_tools']:
            embed = discord.Embed(
                title="❌ Cannot Unload",
                description="Cannot unload the admin/dev_tools cog (you'd lose access to these commands!)",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
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

        except commands.ExtensionNotLoaded:
            embed = discord.Embed(
                title="❌ Cog Not Loaded",
                description=f"Cog `{cog}` is not currently loaded.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            embed = discord.Embed(
                title="❌ Unload Failed",
                description=f"Failed to unload `{cog}`:\n```py\n{str(e)}\n```",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="listcogs", description="List all loaded cogs")
    async def list_cogs(self, interaction: discord.Interaction):
        """List all currently loaded cogs"""
        loaded_cogs = list(self.bot.extensions.keys())

        if not loaded_cogs:
            embed = discord.Embed(
                title="🔧 No Cogs Loaded",
                description="No cogs are currently loaded.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
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
            elif any(word in cog_name.lower() for word in ['admin', 'dev', 'util', 'log']):
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

    @app_commands.command(name="reloadall", description="Reload all loaded cogs")
    async def reload_all_cogs(self, interaction: discord.Interaction):
        """Reload all currently loaded cogs"""
        await interaction.response.defer(ephemeral=True)

        loaded_cogs = list(self.bot.extensions.keys())
        results = {"success": [], "failed": []}

        for cog in loaded_cogs:
            try:
                await self.bot.reload_extension(cog)
                results["success"].append(cog.replace('cogs.', ''))
                self.reload_stats['successful_reloads'] += 1
            except Exception as e:
                results["failed"].append((cog.replace('cogs.', ''), str(e)[:100]))
                self.reload_stats['failed_reloads'] += 1

            self.reload_stats['total_reloads'] += 1

        self.reload_stats['last_reload_time'] = discord.utils.utcnow()

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

        # System info
        embed.add_field(
            name="🤖 Bot Status",
            value=f"**Loaded Cogs:** {len(self.bot.extensions)}\n"
                  f"**Guilds:** {len(self.bot.guilds)}\n"
                  f"**Users:** {len(self.bot.users)}",
            inline=True
        )

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
        await interaction.response.defer(ephemeral=True)

        try:
            if guild_only and interaction.guild:
                synced = await self.bot.tree.sync(guild=interaction.guild)
                embed = discord.Embed(
                    title="✅ Commands Synced (Guild)",
                    description=f"Synced {len(synced)} commands to this guild.",
                    color=discord.Color.green()
                )
            else:
                synced = await self.bot.tree.sync()
                embed = discord.Embed(
                    title="✅ Commands Synced (Global)",
                    description=f"Synced {len(synced)} commands globally.\nMay take up to 1 hour to appear everywhere.",
                    color=discord.Color.green()
                )

            embed.timestamp = discord.utils.utcnow()

        except Exception as e:
            embed = discord.Embed(
                title="❌ Sync Failed",
                description=f"Failed to sync commands:\n```py\n{str(e)}\n```",
                color=discord.Color.red()
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =============================================================================
    # TEXT COMMANDS (QUICK ACCESS)
    # =============================================================================

    @commands.command(name='r', aliases=['reload'])
    @commands.is_owner()
    async def reload_text(self, ctx, *, cog: str):
        """Quick reload command (text version)"""
        try:
            await self.bot.reload_extension(f'cogs.{cog}')
            await ctx.message.add_reaction('✅')
            self.reload_stats['successful_reloads'] += 1
        except Exception as e:
            await ctx.send(f"❌ **Reload Failed:** `{cog}`\n```py\n{str(e)}\n```")
            self.reload_stats['failed_reloads'] += 1

        self.reload_stats['total_reloads'] += 1
        self.reload_stats['last_reload_time'] = discord.utils.utcnow()

    @commands.command(name='ra', aliases=['reloadall'])
    @commands.is_owner()
    async def reload_all_text(self, ctx):
        """Quick reload all command (text version)"""
        loaded_cogs = list(self.bot.extensions.keys())
        success_count = 0

        for cog in loaded_cogs:
            try:
                await self.bot.reload_extension(cog)
                success_count += 1
                self.reload_stats['successful_reloads'] += 1
            except Exception:
                self.reload_stats['failed_reloads'] += 1

            self.reload_stats['total_reloads'] += 1

        self.reload_stats['last_reload_time'] = discord.utils.utcnow()
        await ctx.send(f"🔄 Reloaded {success_count}/{len(loaded_cogs)} cogs")

    @commands.command(name='lc', aliases=['listcogs'])
    @commands.is_owner()
    async def list_cogs_text(self, ctx):
        """Quick list cogs command (text version)"""
        loaded_cogs = [cog.replace('cogs.', '') for cog in self.bot.extensions.keys()]
        if loaded_cogs:
            cog_list = ', '.join([f"`{cog}`" for cog in sorted(loaded_cogs)])
            await ctx.send(f"**Loaded Cogs ({len(loaded_cogs)}):** {cog_list}")
        else:
            await ctx.send("No cogs are currently loaded.")


async def setup(bot):
    await bot.add_cog(DevToolsCog(bot))