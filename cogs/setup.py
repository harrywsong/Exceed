# cogs/setup.py
import discord
from discord.ext import commands
import os
import json
import asyncio
from typing import Optional, Dict, Any
from utils.config import (
    load_server_config,
    save_server_config,
    get_global_config,
    is_server_configured,
    get_channel_id,
    get_role_id,
    is_feature_enabled
)


class MultiServerBotSetup:
    def __init__(self, bot, guild: discord.Guild, user: discord.User):
        self.bot = bot
        self.guild = guild
        self.user = user
        self.config = {}
        self.setup_channel = None
        self.config_file_path = 'data/server_configs.json'

    async def create_setup_channel(self) -> discord.TextChannel:
        """Create a temporary setup channel for configuration"""
        overwrites = {
            self.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            self.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await self.guild.create_text_channel(
            name=f"bot-setup-{self.user.name}",
            overwrites=overwrites,
            reason="Bot setup configuration"
        )

        self.setup_channel = channel
        return channel

    async def send_welcome_message(self):
        """Send initial setup message"""
        embed = discord.Embed(
            title="🎮 [Exceed] Discord Bot Setup",
            description="Welcome to the Exceed bot setup! I'll configure this server for our multi-feature bot.\n\n"
                        "🎯 **Available Features:**\n"
                        "• 🎰 Casino Games (Blackjack, Roulette, Slots, etc.)\n"
                        "• 🏆 Achievement System\n"
                        "• 🎫 Ticket Support System\n"
                        "• 🎤 Voice Channel Management\n"
                        "• 💰 Economy & Coin System\n"
                        "• 📊 Message History & Logging\n"
                        "• 🎭 Reaction Roles\n"
                        "• 👋 Welcome/Goodbye Messages\n\n"
                        "ℹ️ **Setup Process:**\n"
                        "• One bot serves multiple servers with individual configs\n"
                        "• Type `skip` to skip optional features\n"
                        "• Type `cancel` at any time to stop\n\n"
                        f"Setting up for: **{self.guild.name}** ({self.guild.id})\n"
                        "Let's begin! 🚀",
            color=0x7289DA
        )
        embed.set_footer(text="Exceed Bot Setup • This channel will auto-delete after setup")
        await self.setup_channel.send(embed=embed)

    async def get_user_input(self, prompt: str, timeout: int = 300) -> Optional[str]:
        """Get user input with timeout"""
        await self.setup_channel.send(prompt)

        try:
            def check(msg):
                return msg.author == self.user and msg.channel == self.setup_channel

            message = await self.bot.wait_for('message', check=check, timeout=timeout)

            if message.content.lower() == 'cancel':
                await self.setup_channel.send("❌ Setup cancelled.")
                return None

            if message.content.lower() == 'skip':
                return 'skip'

            return message.content.strip()

        except asyncio.TimeoutError:
            await self.setup_channel.send("⏱️ Setup timed out. Please run `/bot-setup` again.")
            return None

    def load_existing_configs(self):
        """Load existing server configurations"""
        try:
            # Ensure data directory exists
            os.makedirs('data', exist_ok=True)

            if os.path.exists(self.config_file_path):
                with open(self.config_file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            print(f"Error loading existing configs: {e}")
            return {}

    async def check_existing_setup(self):
        """Check if this is first-time setup or adding another server"""
        existing_configs = self.load_existing_configs()
        global_config = get_global_config()

        if existing_configs or global_config.get('DISCORD_TOKEN'):
            embed = discord.Embed(
                title="🔍 Existing Configuration Detected",
                description="I found existing bot configurations.",
                color=0xff9900
            )

            if existing_configs:
                server_list = []
                for guild_id, config in list(existing_configs.items())[:5]:
                    guild_name = config.get('guild_name', 'Unknown Server')
                    feature_count = sum(1 for v in config.get('features', {}).values() if v)
                    server_list.append(f"• **{guild_name}** ({guild_id}) - {feature_count} features")

                embed.add_field(
                    name="📊 Configured Servers",
                    value='\n'.join(server_list) +
                          (f'\n*...and {len(existing_configs) - 5} more*' if len(existing_configs) > 5 else ''),
                    inline=False
                )

            if str(self.guild.id) in existing_configs:
                current_config = existing_configs[str(self.guild.id)]
                embed.add_field(
                    name="⚠️ This Server Already Configured",
                    value=f"**Features Enabled**: {sum(1 for v in current_config.get('features', {}).values() if v)}\n"
                          f"**Channels Set**: {len([c for c in current_config.get('channels', {}).values() if c])}\n"
                          f"**Roles Set**: {len([r for r in current_config.get('roles', {}).values() if r])}\n"
                          "*Setup will update existing settings*",
                    inline=False
                )

            await self.setup_channel.send(embed=embed)

            response = await self.get_user_input("Continue with setup? This will update configurations. (yes/no)")
            if response is None or response.lower() not in ['yes', 'y']:
                return False

        return True

    async def setup_server_channels(self):
        """Setup channel configurations for this server"""
        embed = discord.Embed(
            title="📺 Channel Configuration",
            description="Configure channels for various bot features. Mention channels (#channel) or provide IDs.",
            color=0x0099ff
        )
        await self.setup_channel.send(embed=embed)

        server_config = {
            'guild_id': str(self.guild.id),
            'guild_name': self.guild.name,
            'channels': {},
            'roles': {},
            'features': {},
            'settings': {}
        }

        # Core channels
        core_channels = [
            ("log_channel", "📝 **Log Channel**: Where should I send bot logs and admin notifications?"),
            ("welcome_channel", "👋 **Welcome Channel**: Where should I send welcome messages for new members?"),
            ("goodbye_channel", "👋 **Goodbye Channel**: Where should I send goodbye messages?"),
            ("member_chat_channel", "💬 **Member Chat**: Main chat channel for members?"),
        ]

        for config_key, prompt in core_channels:
            response = await self.get_user_input(f"{prompt} (or type `skip`)")
            if response is None:
                return False
            if response == 'skip':
                server_config['channels'][config_key] = None
                continue

            channel_id = await self.parse_channel_mention_or_id(response)
            if channel_id:
                channel = self.guild.get_channel(channel_id)
                server_config['channels'][config_key] = {
                    'id': channel_id,
                    'name': channel.name if channel else 'Unknown'
                }
                await self.setup_channel.send(
                    f"✅ Set {config_key.replace('_', ' ')} to #{channel.name if channel else channel_id}")
            else:
                await self.setup_channel.send("❌ Invalid channel. Skipping.")
                server_config['channels'][config_key] = None

        self.server_config = server_config
        return True

    async def setup_server_roles(self):
        """Setup role configurations"""
        embed = discord.Embed(
            title="🎭 Role Configuration",
            description="Configure important roles for bot features",
            color=0x9932cc
        )
        await self.setup_channel.send(embed=embed)

        role_configs = [
            ("staff_role", "👮 **Staff Role**: Moderators who can use admin commands?"),
            ("admin_role", "👑 **Admin Role**: Administrators with full bot access?"),
            ("member_role", "👤 **Member Role**: Verified members who can use most features?"),
            ("unverified_role", "❓ **Unverified Role**: New users before verification?"),
        ]

        for config_key, prompt in role_configs:
            response = await self.get_user_input(f"{prompt} (mention @role or provide ID, or `skip`)")
            if response is None:
                return False
            if response == 'skip':
                self.server_config['roles'][config_key] = None
                continue

            role_id = await self.parse_role_mention_or_id(response)
            if role_id:
                role = self.guild.get_role(role_id)
                self.server_config['roles'][config_key] = {
                    'id': role_id,
                    'name': role.name if role else 'Unknown'
                }
                await self.setup_channel.send(
                    f"✅ Set {config_key.replace('_', ' ')} to @{role.name if role else role_id}")
            else:
                await self.setup_channel.send("❌ Invalid role. Skipping.")
                self.server_config['roles'][config_key] = None

        return True

    async def setup_casino_features(self):
        """Setup casino and economy features"""
        embed = discord.Embed(
            title="🎰 Casino & Economy Features",
            description="Configure casino games and economy system",
            color=0xffd700
        )
        await self.setup_channel.send(embed=embed)

        response = await self.get_user_input("🎲 Enable casino games? (Blackjack, Roulette, Slots, etc.) (yes/no)")
        if response is None:
            return False

        casino_enabled = response.lower() in ['yes', 'y', 'true']
        self.server_config['features']['casino_games'] = casino_enabled

        if casino_enabled:
            # Casino channel
            response = await self.get_user_input(
                "🎰 **Casino Channel**: Where should casino games be played? (or `skip`)")
            if response and response != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.server_config['channels']['casino_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

            # Economy settings
            response = await self.get_user_input(
                "💰 **Starting Coins**: How many coins should new members get? (default: 1000)")
            if response and response != 'skip':
                try:
                    starting_coins = int(response)
                    self.server_config['settings']['starting_coins'] = starting_coins
                except ValueError:
                    self.server_config['settings']['starting_coins'] = 1000
            else:
                self.server_config['settings']['starting_coins'] = 1000

        return True

    async def setup_achievement_system(self):
        """Setup achievement system"""
        embed = discord.Embed(
            title="🏆 Achievement System",
            description="Configure the achievement and leaderboard system",
            color=0xff6b6b
        )
        await self.setup_channel.send(embed=embed)

        response = await self.get_user_input("🏆 Enable achievement system? (yes/no)")
        if response is None:
            return False

        achievements_enabled = response.lower() in ['yes', 'y', 'true']
        self.server_config['features']['achievements'] = achievements_enabled

        if achievements_enabled:
            # Achievement channels
            response = await self.get_user_input(
                "🏆 **Achievement Channel**: Where should achievements be announced? (or `skip`)")
            if response and response != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.server_config['channels']['achievement_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

            response = await self.get_user_input(
                "📊 **Leaderboard Channel**: Where should leaderboards be posted? (or `skip`)")
            if response and response != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.server_config['channels']['leaderboard_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

        return True

    async def setup_ticket_system(self):
        """Setup ticket support system"""
        embed = discord.Embed(
            title="🎫 Support Ticket System",
            description="Configure support tickets for member assistance",
            color=0xe74c3c
        )
        await self.setup_channel.send(embed=embed)

        response = await self.get_user_input("🎫 Enable support ticket system? (yes/no)")
        if response is None:
            return False

        tickets_enabled = response.lower() in ['yes', 'y', 'true']
        self.server_config['features']['ticket_system'] = tickets_enabled

        if tickets_enabled:
            # Ticket category
            response = await self.get_user_input(
                "📁 **Ticket Category ID**: What category should tickets be created in? (provide category ID)")
            if response and response != 'skip':
                try:
                    category_id = int(response)
                    category = discord.utils.get(self.guild.categories, id=category_id)
                    if category:
                        self.server_config['channels']['ticket_category'] = {
                            'id': category_id,
                            'name': category.name
                        }
                        await self.setup_channel.send(f"✅ Set ticket category to {category.name}")
                    else:
                        await self.setup_channel.send("❌ Category not found.")
                except ValueError:
                    await self.setup_channel.send("❌ Invalid category ID.")

            # Ticket channel for creating tickets
            response = await self.get_user_input("🎫 **Ticket Channel**: Where should users create tickets? (or `skip`)")
            if response and response != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.server_config['channels']['ticket_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

        return True

    async def setup_voice_features(self):
        """Setup voice channel features"""
        embed = discord.Embed(
            title="🎤 Voice Channel Features",
            description="Configure temporary voice channels and voice management",
            color=0x3498db
        )
        await self.setup_channel.send(embed=embed)

        response = await self.get_user_input("🎤 Enable temporary voice channels? (yes/no)")
        if response is None:
            return False

        voice_enabled = response.lower() in ['yes', 'y', 'true']
        self.server_config['features']['voice_channels'] = voice_enabled

        if voice_enabled:
            # Lobby voice channel
            response = await self.get_user_input(
                "🎵 **Lobby Voice Channel**: Which voice channel should be the lobby? (provide voice channel ID or `skip`)")
            if response and response != 'skip':
                try:
                    channel_id = int(response)
                    channel = self.guild.get_channel(channel_id)
                    if channel and isinstance(channel, discord.VoiceChannel):
                        self.server_config['channels']['lobby_voice'] = {
                            'id': channel_id,
                            'name': channel.name
                        }
                        await self.setup_channel.send(f"✅ Set lobby voice to {channel.name}")
                    else:
                        await self.setup_channel.send("❌ Invalid voice channel.")
                except ValueError:
                    await self.setup_channel.send("❌ Invalid channel ID.")

            # Temp voice category
            response = await self.get_user_input(
                "📁 **Temp Voice Category ID**: Which category should temporary voices be created in?")
            if response and response != 'skip':
                try:
                    category_id = int(response)
                    category = discord.utils.get(self.guild.categories, id=category_id)
                    if category:
                        self.server_config['channels']['temp_voice_category'] = {
                            'id': category_id,
                            'name': category.name
                        }
                        await self.setup_channel.send(f"✅ Set temp voice category to {category.name}")
                except ValueError:
                    await self.setup_channel.send("❌ Invalid category ID.")

        return True

    async def setup_additional_features(self):
        """Setup additional bot features"""
        embed = discord.Embed(
            title="⚡ Additional Features",
            description="Enable/disable other bot features",
            color=0x95a5a6
        )
        await self.setup_channel.send(embed=embed)

        additional_features = [
            ("welcome_messages", "👋 Enable welcome/goodbye messages?"),
            ("message_history", "📚 Enable message history logging?"),
            ("reaction_roles", "😀 Enable reaction role system?"),
            ("auto_moderation", "🛡️ Enable auto-moderation features?"),
        ]

        for feature_key, prompt in additional_features:
            response = await self.get_user_input(f"{prompt} (yes/no)")
            if response is None:
                return False
            self.server_config['features'][feature_key] = response.lower() in ['yes', 'y', 'true']

            status = "✅ Enabled" if self.server_config['features'][feature_key] else "❌ Disabled"
            await self.setup_channel.send(f"{status} {feature_key.replace('_', ' ').title()}")

        return True

    async def finalize_setup(self):
        """Save all configurations"""
        try:
            # Load existing server configs
            all_server_configs = self.load_existing_configs()

            # Add/update this server's config
            all_server_configs[str(self.guild.id)] = self.server_config

            # Ensure data directory exists
            os.makedirs('data', exist_ok=True)

            # Save server configs
            with open(self.config_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_server_configs, f, indent=2, ensure_ascii=False)

            # Create summary
            enabled_features = [k.replace('_', ' ').title() for k, v in self.server_config['features'].items() if v]
            configured_channels = len([c for c in self.server_config['channels'].values() if c])
            configured_roles = len([r for r in self.server_config['roles'].values() if r])

            embed = discord.Embed(
                title="✅ Setup Complete!",
                description=f"**{self.guild.name}** has been successfully configured!",
                color=0x00ff00
            )

            embed.add_field(
                name="📊 Configuration Summary",
                value=f"• **Channels Configured**: {configured_channels}\n"
                      f"• **Roles Configured**: {configured_roles}\n"
                      f"• **Features Enabled**: {len(enabled_features)}\n"
                      f"• **Server ID**: `{self.guild.id}`",
                inline=False
            )

            if enabled_features:
                embed.add_field(
                    name="🚀 Enabled Features",
                    value='\n'.join([f"• {feature}" for feature in enabled_features]),
                    inline=False
                )

            embed.add_field(
                name="📄 Next Steps",
                value="• Bot is ready to use in this server\n"
                      "• Test features with slash commands\n"
                      "• Configure reaction roles if enabled\n"
                      "• Invite members and start using the bot!",
                inline=False
            )

            embed.add_field(
                name="🗑️ Cleanup",
                value="This setup channel will be deleted in 30 seconds.",
                inline=False
            )

            await self.setup_channel.send(embed=embed)

            # Schedule cleanup
            await asyncio.sleep(30)
            await self.setup_channel.delete(reason="Setup completed")

            return True

        except Exception as e:
            await self.setup_channel.send(f"❌ Error saving configuration: {e}")
            return False

    async def parse_channel_mention_or_id(self, text: str) -> Optional[int]:
        """Parse channel mention or ID"""
        if text.startswith('<#') and text.endswith('>'):
            try:
                return int(text[2:-1])
            except ValueError:
                return None

        try:
            channel_id = int(text)
            channel = self.guild.get_channel(channel_id)
            return channel_id if channel else None
        except ValueError:
            return None

    async def parse_role_mention_or_id(self, text: str) -> Optional[int]:
        """Parse role mention or ID"""
        if text.startswith('<@&') and text.endswith('>'):
            try:
                return int(text[3:-1])
            except ValueError:
                return None

        try:
            role_id = int(text)
            role = self.guild.get_role(role_id)
            return role_id if role else None
        except ValueError:
            return None

    async def run_setup(self):
        """Run the complete setup process"""
        try:
            await self.create_setup_channel()
            await self.send_welcome_message()

            # Check existing setup
            if not await self.check_existing_setup():
                await self.setup_channel.delete(reason="Setup cancelled")
                return

            # Run setup steps
            setup_steps = [
                self.setup_server_channels,
                self.setup_server_roles,
                self.setup_casino_features,
                self.setup_achievement_system,
                self.setup_ticket_system,
                self.setup_voice_features,
                self.setup_additional_features,
                self.finalize_setup
            ]

            for step in setup_steps:
                success = await step()
                if not success:
                    await self.setup_channel.send("❌ Setup cancelled or failed.")
                    await asyncio.sleep(10)
                    await self.setup_channel.delete(reason="Setup failed")
                    return

        except Exception as e:
            if self.setup_channel:
                await self.setup_channel.send(f"❌ An error occurred: {e}")
                await asyncio.sleep(10)
                try:
                    await self.setup_channel.delete(reason="Setup error")
                except:
                    pass


class SetupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="bot-setup", description="Setup the Exceed bot for this server")
    async def setup_bot(self, ctx):
        if not ctx.author.guild_permissions.administrator:
            await ctx.respond("❌ You need Administrator permissions to run bot setup.", ephemeral=True)
            return

        await ctx.respond("🔧 Starting Exceed bot setup... Creating setup channel...", ephemeral=True)

        setup = MultiServerBotSetup(self.bot, ctx.guild, ctx.author)
        await setup.run_setup()

    @discord.slash_command(name="bot-status", description="Check bot configuration status")
    async def bot_status(self, ctx):
        """Show current bot configuration status"""
        try:
            config = load_server_config(ctx.guild.id)

            if not config:
                embed = discord.Embed(
                    title="❌ Server Not Configured",
                    description=f"**{ctx.guild.name}** is not configured yet.\n\nRun `/bot-setup` to set up the bot.",
                    color=0xe74c3c
                )
            else:
                enabled_features = [k.replace('_', ' ').title() for k, v in config.get('features', {}).items() if v]
                configured_channels = len([c for c in config.get('channels', {}).values() if c])
                configured_roles = len([r for r in config.get('roles', {}).values() if r])

                embed = discord.Embed(
                    title="✅ Server Configuration",
                    description=f"**{ctx.guild.name}** is properly configured!",
                    color=0x00ff00
                )

                embed.add_field(
                    name="📊 Configuration Stats",
                    value=f"• **Channels**: {configured_channels} configured\n"
                          f"• **Roles**: {configured_roles} configured\n"
                          f"• **Features**: {len(enabled_features)} enabled",
                    inline=True
                )

                if enabled_features:
                    embed.add_field(
                        name="🚀 Enabled Features",
                        value='\n'.join([f"• {feature}" for feature in enabled_features[:8]]) +
                              ('\n• *...and more*' if len(enabled_features) > 8 else ''),
                        inline=True
                    )

            # Global bot stats
            try:
                with open('data/server_configs.json', 'r', encoding='utf-8') as f:
                    all_configs = json.load(f)
                total_servers = len(all_configs)
            except:
                total_servers = 0

            embed.add_field(
                name="🌐 Global Stats",
                value=f"• **Total Configured Servers**: {total_servers}\n"
                      f"• **Bot Active In**: {len(self.bot.guilds)} servers\n"
                      f"• **Total Users**: {sum(guild.member_count for guild in self.bot.guilds)}",
                inline=False
            )

            await ctx.respond(embed=embed, ephemeral=True)

        except Exception as e:
            await ctx.respond(f"❌ Error checking status: {e}", ephemeral=True)


def setup(bot):
    bot.add_cog(SetupCog(bot))