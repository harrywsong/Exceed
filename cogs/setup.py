# cogs/setup.py
import traceback

import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import asyncio
from typing import Optional, Dict, Any
from dotenv import dotenv_values

from utils.config import (
    load_server_config,
    save_server_config,
    get_global_config,
    is_server_configured,
    get_channel_id,
    get_role_id,
    is_feature_enabled
)
# Assuming get_logger is available and configured as per previous examples
from utils.logger import get_logger


class MultiServerBotSetup:
    def __init__(self, bot, guild: discord.Guild, user: discord.User):
        self.bot = bot
        self.guild = guild
        self.user = user
        self.logger = get_logger("BotSetup")  # Initialize logger for this cog
        self.config = {
            'guild_id': str(self.guild.id),
            'guild_name': self.guild.name,
            'channels': {},
            'roles': {},
            'features': {},
            'settings': {},
            'reaction_roles': {}
        }
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
            title="🎮 [아날로그] Discord Bot Setup",
            description="Welcome to the 아날로그 bot setup! I'll configure this server for our multi-feature bot.\n\n"
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
        embed.set_footer(text="아날로그 Bot Setup • This channel will auto-delete after setup")
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
            os.makedirs('data', exist_ok=True)
            if os.path.exists(self.config_file_path):
                with open(self.config_file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            # Log error with guild_id context
            self.logger.error(f"Error loading existing configs for guild {self.guild.id}: {e}",
                              extra={'guild_id': self.guild.id})
            return {}

    async def check_existing_setup(self):
        """Check if this is first-time setup or adding another server"""
        existing_configs = self.load_existing_configs()
        global_config = get_global_config()

        if existing_configs or global_config.get('DISCORD_TOKEN'):
            embed = discord.Embed(
                title="📋 Existing Configuration Detected",
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
                # Log cancellation with guild_id context
                self.logger.info(f"Setup cancelled by user for guild {self.guild.id}",
                                 extra={'guild_id': self.guild.id})
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

        # Core channels
        core_channels = [
            ("log_channel", "📝 **Log Channel**: Where should I send bot logs and admin notifications?"),
            ("welcome_channel", "👋 **Welcome Channel**: Where should I send welcome messages for new members?"),
            ("goodbye_channel", "👋 **Goodbye Channel**: Where should I send goodbye messages?"),
            ("member_chat_channel", "💬 **Member Chat**: Main chat channel for members?"),
            ("message_history_channel",
             "📜 **Message History Channel**: Where should deleted/edited message logs be sent?"),
        ]

        for config_key, prompt in core_channels:
            response = await self.get_user_input(f"{prompt} (or type `skip`)")
            if response is None:
                return False
            if response.lower() == 'skip':
                self.config['channels'][config_key] = None
                continue

            channel_id = await self.parse_channel_mention_or_id(response)
            if channel_id:
                channel = self.guild.get_channel(channel_id)
                self.config['channels'][config_key] = {
                    'id': channel_id,
                    'name': channel.name if channel else 'Unknown'
                }
                await self.setup_channel.send(
                    f"✅ Set {config_key.replace('_', ' ')} to #{channel.name if channel else channel_id}")
            else:
                await self.setup_channel.send("❌ Invalid channel. Skipping.")
                self.config['channels'][config_key] = None

        # Log completion with guild_id context
        self.logger.info(f"Channel configuration step completed for guild {self.guild.id}",
                         extra={'guild_id': self.guild.id})
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
            if response.lower() == 'skip':
                self.config['roles'][config_key] = None
                continue

            role_id = await self.parse_role_mention_or_id(response)
            if role_id:
                role = self.guild.get_role(role_id)
                self.config['roles'][config_key] = {
                    'id': role_id,
                    'name': role.name if role else 'Unknown'
                }
                await self.setup_channel.send(
                    f"✅ Set {config_key.replace('_', ' ')} to @{role.name if role else role_id}")
            else:
                await self.setup_channel.send("❌ Invalid role. Skipping.")
                self.config['roles'][config_key] = None

        # Log completion with guild_id context
        self.logger.info(f"Role configuration step completed for guild {self.guild.id}",
                         extra={'guild_id': self.guild.id})
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
        self.config['features']['casino_games'] = casino_enabled

        if casino_enabled:
            casino_channels = [
                ("slots_channel", "🍒 **Slots Channel**"),
                ("blackjack_channel", "🃏 **Blackjack Channel**"),
                ("hilow_channel", "📈 **Hi-Lo Channel**"),
                ("dice_channel", "🎲 **Dice Channel**"),
                ("roulette_channel", "🔴 **Roulette Channel**"),
                ("lottery_channel", "🎟️ **Lottery Channel**"),
                ("coinflip_channel", "🪙 **Coin Toss Channel**"),
                ("minesweeper_channel", "💣 **Minesweeper Channel**"),
                ("bingo_channel", "🅱️ **Bingo Channel**"),
                ("crash_channel", "✈️ **Crash Channel**")
            ]

            for config_key, prompt in casino_channels:
                response = await self.get_user_input(f"{prompt}: Where should this game be hosted? (or `skip`)")
                if response and response.lower() != 'skip':
                    channel_id = await self.parse_channel_mention_or_id(response)
                    if channel_id:
                        channel = self.guild.get_channel(channel_id)
                        self.config['channels'][config_key] = {
                            'id': channel_id,
                            'name': channel.name if channel else 'Unknown'
                        }
                    else:
                        await self.setup_channel.send("❌ Invalid channel. Skipping.")

            # Economy settings
            response = await self.get_user_input(
                "💰 **Starting Coins**: How many coins should new members get? (default: 1000)")
            if response and response.lower() != 'skip':
                try:
                    starting_coins = int(response)
                    self.config['settings']['starting_coins'] = starting_coins
                except ValueError:
                    self.config['settings']['starting_coins'] = 1000
            else:
                self.config['settings']['starting_coins'] = 1000

        # Log completion with guild_id context
        self.logger.info(f"Casino features setup completed for guild {self.guild.id}",
                         extra={'guild_id': self.guild.id})
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
        self.config['features']['achievements'] = achievements_enabled

        if achievements_enabled:
            # Achievement announcement channel
            response = await self.get_user_input(
                "📣 **Achievement Announcements Channel**: Where should achievements be announced? (or `skip`)")
            if response and response.lower() != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.config['channels']['achievement_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

            # Achievement alert channel (more general alerts)
            response = await self.get_user_input(
                "🚨 **Achievement Alert Channel**: For general achievement alerts/logs. (or `skip`)")
            if response and response.lower() != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.config['channels']['achievement_alert_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

            # Leaderboard channel
            response = await self.get_user_input(
                "📊 **Leaderboard Channel**: Where should leaderboards be posted? (or `skip`)")
            if response and response.lower() != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.config['channels']['leaderboard_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

        # Log completion with guild_id context
        self.logger.info(f"Achievement system setup completed for guild {self.guild.id}",
                         extra={'guild_id': self.guild.id})
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
        self.config['features']['ticket_system'] = tickets_enabled

        if tickets_enabled:
            # Ticket category
            response = await self.get_user_input(
                "📁 **Ticket Category ID**: What category should tickets be created in? (provide category ID)")
            if response and response.lower() != 'skip':
                try:
                    category_id = int(response)
                    category = discord.utils.get(self.guild.categories, id=category_id)
                    if category:
                        self.config['channels']['ticket_category'] = {
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
            if response and response.lower() != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.config['channels']['ticket_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

            # Ticket history channel
            response = await self.get_user_input(
                "📜 **Ticket History Channel**: Where should closed ticket transcripts be sent? (or `skip`)")
            if response and response.lower() != 'skip':
                channel_id = await self.parse_channel_mention_or_id(response)
                if channel_id:
                    channel = self.guild.get_channel(channel_id)
                    self.config['channels']['ticket_history_channel'] = {
                        'id': channel_id,
                        'name': channel.name if channel else 'Unknown'
                    }

        # Log completion with guild_id context
        self.logger.info(f"Ticket system setup completed for guild {self.guild.id}", extra={'guild_id': self.guild.id})
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
        self.config['features']['voice_channels'] = voice_enabled

        if voice_enabled:
            # Temp voice category
            response = await self.get_user_input(
                "📁 **Temp Voice Category ID**: Which category should temporary voices be created in?")
            if response and response.lower() != 'skip':
                try:
                    category_id = int(response)
                    category = discord.utils.get(self.guild.categories, id=category_id)
                    if category:
                        self.config['channels']['temp_voice_category'] = {
                            'id': category_id,
                            'name': category.name
                        }
                        await self.setup_channel.send(f"✅ Set temp voice category to {category.name}")
                except ValueError:
                    await self.setup_channel.send("❌ Invalid category ID.")

            # Lobby voice channel
            response = await self.get_user_input(
                "🎵 **Lobby Voice Channel**: Which voice channel should be the lobby? (provide voice channel ID or `skip`)")
            if response and response.lower() != 'skip':
                try:
                    channel_id = int(response)
                    channel = self.guild.get_channel(channel_id)
                    if channel and isinstance(channel, discord.VoiceChannel):
                        self.config['channels']['lobby_voice'] = {
                            'id': channel_id,
                            'name': channel.name
                        }
                        await self.setup_channel.send(f"✅ Set lobby voice to {channel.name}")
                    else:
                        await self.setup_channel.send("❌ Invalid voice channel.")
                except ValueError:
                    await self.setup_channel.send("❌ Invalid channel ID.")

        # Log completion with guild_id context
        self.logger.info(f"Voice features setup completed for guild {self.guild.id}", extra={'guild_id': self.guild.id})
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
            ("auto_moderation", "🛡️ Enable auto-moderation features?"),
            ("reaction_roles", "😀 Enable reaction role system?"),
        ]

        for feature_key, prompt in additional_features:
            response = await self.get_user_input(f"{prompt} (yes/no)")
            if response is None:
                return False
            self.config['features'][feature_key] = response.lower() in ['yes', 'y', 'true']
            status = "✅ Enabled" if self.config['features'][feature_key] else "❌ Disabled"
            await self.setup_channel.send(f"{status} {feature_key.replace('_', ' ').title()}")

        # Log completion with guild_id context
        self.logger.info(f"Additional features setup completed for guild {self.guild.id}",
                         extra={'guild_id': self.guild.id})
        return True

    async def setup_reaction_roles(self):
        """Setup reaction role system"""
        if not self.config['features'].get('reaction_roles'):
            return True

        embed = discord.Embed(
            title="😀 Reaction Role Setup",
            description="Configure the reaction roles. You will provide a message ID and then emoji-role pairs.",
            color=0x3498db
        )
        await self.setup_channel.send(embed=embed)

        while True:
            response = await self.get_user_input(
                "💬 **Reaction Message ID**: Enter the message ID for the reaction roles (or `done` to finish).")
            if response is None:
                return False
            if response.lower() == 'done':
                break

            try:
                message_id = int(response)
                self.config['reaction_roles'][str(message_id)] = {}
                await self.setup_channel.send(f"✅ Message ID {message_id} accepted. Now, add emoji-role pairs.")
            except ValueError:
                await self.setup_channel.send("❌ Invalid message ID. Please try again.")
                continue

            # Get emoji-role pairs
            while True:
                pair_response = await self.get_user_input(
                    "💡 **Emoji & Role**: Enter an emoji and the role ID, separated by a comma (e.g., `👍,123456789`) or `done`.")
                if pair_response is None:
                    return False
                if pair_response.lower() == 'done':
                    break

                parts = pair_response.split(',')
                if len(parts) != 2:
                    await self.setup_channel.send("❌ Invalid format. Please use `emoji,role_id`.")
                    continue

                emoji_str = parts[0].strip()
                try:
                    role_id = int(parts[1].strip())
                    role = self.guild.get_role(role_id)
                    if role:
                        self.config['reaction_roles'][str(message_id)][emoji_str] = role_id
                        await self.setup_channel.send(f"✅ Added {emoji_str} -> @{role.name}")
                    else:
                        await self.setup_channel.send("❌ Role not found. Please use a valid role ID.")
                except ValueError:
                    await self.setup_channel.send("❌ Invalid role ID.")

        # Log completion with guild_id context
        self.logger.info(f"Reaction roles setup completed for guild {self.guild.id}", extra={'guild_id': self.guild.id})
        return True

    async def finalize_setup(self):
        """Save all configurations"""
        try:
            # Load existing server configs
            all_server_configs = self.load_existing_configs()
            # Add/update this server's config
            all_server_configs[str(self.guild.id)] = self.config
            # Ensure data directory exists
            os.makedirs('data', exist_ok=True)
            # Save server configs
            with open(self.config_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_server_configs, f, indent=2, ensure_ascii=False)

            # Log successful save with guild_id context
            self.logger.info(f"Server configuration saved successfully for guild {self.guild.id}",
                             extra={'guild_id': self.guild.id})

            # Create summary
            enabled_features = [k.replace('_', ' ').title() for k, v in self.config['features'].items() if v]
            configured_channels = len([c for c in self.config['channels'].values() if c])
            configured_roles = len([r for r in self.config['roles'].values() if r])
            configured_reaction_roles = len(self.config.get('reaction_roles', {}))
            embed = discord.Embed(
                title="✅ Setup Complete!",
                description=f"**{self.guild.name}** has been successfully configured!",
                color=0x00ff00
            )
            embed.add_field(
                name="📊 Configuration Summary",
                value=f"• **Channels Configured**: {configured_channels}\n"
                      f"• **Roles Configured**: {configured_roles}\n"
                      f"• **Reaction Roles Configured**: {configured_reaction_roles}\n"
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
                name="🔄 Next Steps",
                value="• Bot is ready to use in this server\n"
                      "• Test features with slash commands\n"
                      "• Invite members and start using the bot!",
                inline=False
            )
            embed.add_field(
                name="🗑️ Cleanup",
                value="This setup channel will be deleted in 30 seconds.",
                inline=False
            )
            await self.setup_channel.send(embed=embed)
            await asyncio.sleep(30)
            await self.setup_channel.delete(reason="Setup completed")
            return True
        except Exception as e:
            # Log error with guild_id context
            self.logger.error(f"Error saving configuration for guild {self.guild.id}: {e}\n{traceback.format_exc()}",
                              extra={'guild_id': self.guild.id})
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

    async def migrate_from_env_backup(self):
        """Pre-fill configuration from a .env.backup file if it exists."""
        # NOTE: This part assumes a specific backup file name. You might want to generalize this.
        backup_file = '.env.backup_20250916_181843'  # Replace with actual dynamic finding if needed
        if os.path.exists(backup_file):
            env_vars = dotenv_values(backup_file)

            # Channel IDs
            channel_mappings = {
                "LOG_CHANNEL_ID": "log_channel",
                "LOBBY_VOICE_CHANNEL_ID": "lobby_voice",
                "TEMP_VOICE_CATEGORY_ID": "temp_voice_category",
                "HISTORY_CHANNEL_ID": "ticket_history_channel",
                "TICKET_CHANNEL_ID": "ticket_channel",
                "TICKET_CATEGORY_ID": "ticket_category",
                "WELCOME_CHANNEL_ID": "welcome_channel",
                "GOODBYE_CHANNEL_ID": "goodbye_channel",
                "ACHIEVEMENT_ANNOUNCEMENT_CHANNEL_ID": "achievement_channel",
                "LEADERBOARD_CHANNEL_ID": "leaderboard_channel",
                "MESSAGE_HISTORY_CHANNEL_ID": "message_history_channel"
            }
            for env_key, config_key in channel_mappings.items():
                if env_key in env_vars and env_vars[env_key]:
                    try:
                        channel_id = int(env_vars[env_key])
                        self.config['channels'][config_key] = {'id': channel_id, 'name': 'Migrated'}
                    except ValueError:
                        self.logger.warning(f"Invalid channel ID in backup for {env_key}: {env_vars[env_key]}",
                                            extra={'guild_id': self.guild.id})

            # Role IDs
            role_mappings = {
                "STAFF_ROLE_ID": "staff_role",
                "ADMIN_ROLE_ID": "admin_role",
                "MEMBER_ROLE_ID": "member_role",
                "UNVERIFIED_ROLE_ID": "unverified_role"
            }
            for env_key, config_key in role_mappings.items():
                if env_key in env_vars and env_vars[env_key]:
                    try:
                        role_id = int(env_vars[env_key])
                        self.config['roles'][config_key] = {'id': role_id, 'name': 'Migrated'}
                    except ValueError:
                        self.logger.warning(f"Invalid role ID in backup for {env_key}: {env_vars[env_key]}",
                                            extra={'guild_id': self.guild.id})

            # Reaction Roles
            if "REACTION_ROLES" in env_vars and env_vars["REACTION_ROLES"]:
                try:
                    rr_data = json.loads(env_vars["REACTION_ROLES"].replace("'", '"'))
                    self.config['reaction_roles'] = rr_data
                except json.JSONDecodeError:
                    self.logger.error("Failed to decode REACTION_ROLES JSON from backup.",
                                      extra={'guild_id': self.guild.id})

            self.logger.info(f"Successfully migrated configuration from {backup_file} for guild {self.guild.id}",
                             extra={'guild_id': self.guild.id})

    async def run_setup(self):
        """Run the complete setup process"""
        try:
            await self.create_setup_channel()
            await self.send_welcome_message()

            # Optional: Migrate from existing .env.backup file
            response = await self.get_user_input("Do you want to pre-fill settings from the .env.backup file? (yes/no)")
            if response and response.lower() in ['yes', 'y']:
                await self.migrate_from_env_backup()
                await self.setup_channel.send(
                    "✅ Configuration pre-filled from `.env.backup`! You can now review and update.")
            else:
                self.logger.debug(f"Skipped migration from .env.backup for guild {self.guild.id}",
                                  extra={'guild_id': self.guild.id})

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
                self.setup_reaction_roles,
                self.finalize_setup
            ]

            for step in setup_steps:
                # Log each step's start
                step_name = step.__name__.replace('setup_', '').replace('_', ' ').title()
                self.logger.info(f"Starting configuration step: '{step_name}' for guild {self.guild.id}",
                                 extra={'guild_id': self.guild.id})

                success = await step()
                if not success:
                    await self.setup_channel.send("❌ Setup cancelled or failed.")
                    self.logger.warning(
                        f"Setup process failed or was cancelled at step '{step_name}' for guild {self.guild.id}",
                        extra={'guild_id': self.guild.id})
                    await asyncio.sleep(10)
                    await self.setup_channel.delete(reason="Setup failed")
                    return
                self.logger.info(f"Step '{step_name}' completed successfully for guild {self.guild.id}",
                                 extra={'guild_id': self.guild.id})


        except Exception as e:
            # Log error with guild_id context
            self.logger.error(
                f"An unexpected error occurred during setup for guild {self.guild.id}: {e}\n{traceback.format_exc()}",
                extra={'guild_id': self.guild.id})
            if self.setup_channel:
                await self.setup_channel.send(f"❌ An unexpected error occurred: {e}")
                await asyncio.sleep(10)
                try:
                    await self.setup_channel.delete(reason="Setup error")
                except:
                    pass


class SetupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("SetupCog")  # Initialize logger for this cog

    @app_commands.command(name="bot-setup", description="Setup the bot's features for this server.")
    @app_commands.checks.has_permissions(administrator=True)  # Only administrators can run this command
    async def slash_bot_setup(self, interaction: discord.Interaction):
        """Initiates the bot setup process for the current server."""
        guild = interaction.guild
        user = interaction.user

        # Log command usage with guild_id context
        self.logger.info(f"User {user.display_name} ({user.id}) initiated bot setup in guild {guild.name} ({guild.id})",
                         extra={'guild_id': guild.id})

        # Check if the bot is already configured for this server
        if is_server_configured(guild.id):
            embed = discord.Embed(
                title="🔄 Re-Configuration",
                description="This server is already configured. Running setup again will **update** existing settings.\n\n"
                            "Do you want to proceed with re-configuration?",
                color=0xff9900
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            view = discord.ui.View()
            # Confirm button
            confirm_button = discord.ui.Button(label="Yes, Re-configure", style=discord.ButtonStyle.danger,
                                               custom_id="confirm_reconfig")
            # Cancel button
            cancel_button = discord.ui.Button(label="No, Cancel", style=discord.ButtonStyle.secondary,
                                              custom_id="cancel_reconfig")

            view.add_item(confirm_button)
            view.add_item(cancel_button)

            # Wait for interaction response
            await interaction.followup.send(view=view, ephemeral=True)

            def check(interaction_response: discord.Interaction):
                return (interaction_response.user.id == user.id and
                        interaction_response.channel_id == interaction.channel_id and
                        interaction_response.data['custom_id'] in ['confirm_reconfig', 'cancel_reconfig'])

            try:
                interaction_response, _ = await self.bot.wait_for("interaction", check=check, timeout=60)
            except asyncio.TimeoutError:
                await interaction.followup.send("Configuration re-run timed out.", ephemeral=True)
                return

            if interaction_response.data['custom_id'] == 'cancel_reconfig':
                await interaction_response.response.edit_message(content="Re-configuration cancelled.", embed=None,
                                                                 view=None)
                self.logger.info(
                    f"Re-configuration cancelled by user {user.display_name} ({user.id}) in guild {guild.name} ({guild.id})",
                    extra={'guild_id': guild.id})
                return
            else:
                await interaction_response.response.edit_message(content="Starting re-configuration process...",
                                                                 embed=None, view=None)

        # Proceed with setup
        setup_instance = MultiServerBotSetup(self.bot, guild, user)
        await setup_instance.run_setup()

    @slash_bot_setup.error
    async def bot_setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handles errors for the bot-setup command."""
        guild_id = interaction.guild.id if interaction.guild else "Unknown"
        user_id = interaction.user.id if interaction.user else "Unknown"

        if isinstance(error, app_commands.MissingPermissions):
            # Log missing permissions with guild_id context
            self.logger.warning(
                f"User {user_id} tried to use /bot-setup without administrator permissions in guild {guild_id}.",
                extra={'guild_id': guild_id})
            await interaction.response.send_message("❌ You need administrator permissions to set up the bot.",
                                                    ephemeral=True)
        else:
            # Log other errors with guild_id context
            self.logger.error(
                f"Error in /bot-setup command for user {user_id} in guild {guild_id}: {error}\n{traceback.format_exc()}",
                extra={'guild_id': guild_id})
            await interaction.response.send_message(
                "An error occurred while trying to run the setup command. Please try again later or contact support.",
                ephemeral=True)

    # Add listeners for guild join/remove if not already present in other cogs
    # This is generally handled at the bot level or in a dedicated cog for guild events.
    # If this cog is solely for setup, these might be redundant.
    # However, for comprehensive logging, it's good to log these events here too.

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Logs when the bot joins a new guild."""
        # Log event with guild_id context
        self.logger.info(f"Bot joined new guild: {guild.name} (ID: {guild.id})", extra={'guild_id': guild.id})

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Logs when the bot is removed from a guild."""
        # Log event with guild_id context
        self.logger.info(f"Bot left guild: {guild.name} (ID: {guild.id})", extra={'guild_id': guild.id})


async def setup(bot):
    await bot.add_cog(SetupCog(bot))