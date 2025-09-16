# cogs/coins.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import traceback
import json
import os
from datetime import datetime, timezone, timedelta
import pytz

from utils.logger import get_logger
from utils import config


class CoinsView(discord.ui.View):
    """Persistent view for claiming daily coins"""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="💰 일일 코인 받기", style=discord.ButtonStyle.green, custom_id="claim_daily_coins")
    async def claim_daily_coins(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if casino games are enabled for this server
        if not config.is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message(
                "❌ 이 서버에서는 코인 시스템이 비활성화되어 있습니다.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        guild_id = interaction.guild.id
        eastern = pytz.timezone('America/New_York')
        now = datetime.now(eastern)  # This is timezone-aware
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)  # Still timezone-aware

        try:
            # Check if user already claimed today (guild-specific)
            check_query = """
                    SELECT last_claim_date FROM user_coins 
                    WHERE user_id = $1 AND guild_id = $2
                """
            row = await self.bot.pool.fetchrow(check_query, user_id, guild_id)

            if row and row['last_claim_date']:
                # The database returns a naive datetime, so we need to make it timezone-aware
                last_claim = row['last_claim_date']

                # If last_claim is naive, assume it's in EST and make it timezone-aware
                if last_claim.tzinfo is None:
                    last_claim = eastern.localize(last_claim)
                else:
                    # If it already has timezone info, convert to eastern
                    last_claim = last_claim.astimezone(eastern)

                if last_claim >= today_start:
                    next_claim = (today_start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S EST")
                    await interaction.followup.send(
                        f"❌ 오늘은 이미 코인을 받았습니다!\n다음 받기: {next_claim}",
                        ephemeral=True
                    )
                    return

            # Get starting coins amount from server settings
            starting_coins = config.get_server_setting(guild_id, 'starting_coins', 50)

            # Give daily coins using the add_coins method to trigger leaderboard update
            coins_cog = self.bot.get_cog('CoinsCog')
            if coins_cog:
                # Store as naive datetime in the database (convert timezone-aware to naive)
                naive_now = now.replace(tzinfo=None)

                # Update the database directly for daily claims to include last_claim_date
                update_query = """
                        INSERT INTO user_coins (user_id, guild_id, coins, last_claim_date, total_earned)
                        VALUES ($1, $2, $3, $4, $3)
                        ON CONFLICT (user_id, guild_id) 
                        DO UPDATE SET 
                            coins = user_coins.coins + $3,
                            total_earned = user_coins.total_earned + $3,
                            last_claim_date = EXCLUDED.last_claim_date
                        RETURNING coins
                    """
                result = await self.bot.pool.fetchrow(update_query, user_id, guild_id, starting_coins, naive_now)

                # Log transaction
                await self.bot.pool.execute("""
                        INSERT INTO coin_transactions (user_id, guild_id, amount, transaction_type, description)
                        VALUES ($1, $2, $3, $4, $5)
                    """, user_id, guild_id, starting_coins, "daily_claim", "Daily coin claim")

                # Trigger leaderboard update
                self.bot.loop.create_task(coins_cog.schedule_leaderboard_update(guild_id))

                embed = discord.Embed(
                    title="💰 일일 코인 지급!",
                    description=f"✅ {starting_coins} 코인을 받았습니다!\n현재 잔액: **{result['coins']} 코인**",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text="다음 받기는 내일 자정(EST)에 가능합니다")

                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
            self.bot.logger.error(f"Daily coin claim error for {user_id} in guild {guild_id}: {e}")


class LeaderboardView(discord.ui.View):
    """Persistent view for coin leaderboard navigation"""

    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.current_page = 0
        self.users_per_page = 10

    async def get_leaderboard_data(self):
        """Get leaderboard data from database for this guild"""
        query = """
            SELECT user_id, coins 
            FROM user_coins 
            WHERE coins > 0 AND guild_id = $1
            ORDER BY coins DESC 
            LIMIT 100
        """
        return await self.bot.pool.fetch(query, self.guild_id)

    async def create_leaderboard_embed(self, page=0):
        """Create leaderboard embed for specific page"""
        data = await self.get_leaderboard_data()

        if not data:
            embed = discord.Embed(
                title="🏆 코인 리더보드",
                description="아직 코인 데이터가 없습니다.",
                color=discord.Color.gold()
            )
            return embed

        total_pages = (len(data) - 1) // self.users_per_page + 1
        page = max(0, min(page, total_pages - 1))

        start_idx = page * self.users_per_page
        end_idx = start_idx + self.users_per_page
        page_data = data[start_idx:end_idx]

        embed = discord.Embed(
            title="🏆 코인 리더보드",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )

        leaderboard_text = ""
        for idx, record in enumerate(page_data, start=start_idx + 1):
            try:
                user = self.bot.get_user(record['user_id'])
                username = user.display_name if user else f"Unknown User ({record['user_id']})"

                # Add medal emojis for top 3
                if idx == 1:
                    medal = "🥇"
                elif idx == 2:
                    medal = "🥈"
                elif idx == 3:
                    medal = "🥉"
                else:
                    medal = f"`{idx:2d}.`"

                leaderboard_text += f"{medal} **{username}** - {record['coins']:,} 코인\n"
            except:
                leaderboard_text += f"`{idx:2d}.` Unknown User - {record['coins']:,} 코인\n"

        embed.description = leaderboard_text or "데이터를 불러올 수 없습니다."
        embed.set_footer(text=f"페이지 {page + 1}/{total_pages} • 총 {len(data)}명")

        return embed

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary, custom_id="leaderboard_prev")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        if self.current_page > 0:
            self.current_page -= 1
            embed = await self.create_leaderboard_embed(self.current_page)
            await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary, custom_id="leaderboard_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        data = await self.get_leaderboard_data()
        total_pages = (len(data) - 1) // self.users_per_page + 1 if data else 1

        if self.current_page < total_pages - 1:
            self.current_page += 1
            embed = await self.create_leaderboard_embed(self.current_page)
            await interaction.edit_original_response(embed=embed, view=self)


class CoinsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("코인 시스템")

        # Spam protection - user_id: last_command_time
        self.last_command_time = {}
        self.cooldown_seconds = 3

        # Per-guild leaderboard management
        self.guild_leaderboard_data = {}  # guild_id: message_info
        self.guild_claim_data = {}  # guild_id: message_info

        # Real-time update controls per guild
        self.pending_leaderboard_updates = {}  # guild_id: bool
        self.update_delay = 3  # seconds to debounce updates
        self.last_leaderboard_cache = {}  # guild_id: data

        # Message ID persistence per guild
        self.message_ids_file = "data/guild_message_ids.json"

        self.logger.info("코인 시스템이 초기화되었습니다.")

        # Start tasks after bot is ready
        self.bot.loop.create_task(self.wait_and_start_tasks())

    def has_admin_permissions(self, member: discord.Member) -> bool:
        """Check if member has admin permissions"""
        # Check if user has administrator permissions
        if member.guild_permissions.administrator:
            return True

        # Check if user has the specific admin role for this guild
        admin_role_id = config.get_role_id(member.guild.id, 'admin_role')
        if admin_role_id:
            admin_role = discord.utils.get(member.roles, id=admin_role_id)
            return admin_role is not None

        # Fallback to staff role if admin role not configured
        staff_role_id = config.get_role_id(member.guild.id, 'staff_role')
        if staff_role_id:
            staff_role = discord.utils.get(member.roles, id=staff_role_id)
            return staff_role is not None

        return False

    async def wait_and_start_tasks(self):
        """Wait for bot to be ready then start tasks"""
        await self.bot.wait_until_ready()
        await self.setup_database()
        await self.load_message_ids()

        # Setup initial leaderboards for all configured guilds
        all_configs = config.get_all_server_configs()
        for guild_id_str, guild_config in all_configs.items():
            if guild_config.get('features', {}).get('casino_games'):
                guild_id = int(guild_id_str)
                await self.setup_initial_leaderboard(guild_id)

        # Start maintenance task
        self.maintenance_leaderboard_update.start()

    async def load_message_ids(self):
        """Load persistent message IDs from file"""
        try:
            if os.path.exists(self.message_ids_file):
                with open(self.message_ids_file, 'r') as f:
                    data = json.load(f)
                    self.guild_leaderboard_data = data.get('leaderboard', {})
                    self.guild_claim_data = data.get('claim', {})
                    self.logger.info("Loaded guild message IDs")
        except Exception as e:
            self.logger.error(f"Error loading message IDs: {e}")

    async def save_message_ids(self):
        """Save message IDs to file for persistence"""
        try:
            os.makedirs(os.path.dirname(self.message_ids_file), exist_ok=True)

            data = {
                'leaderboard': self.guild_leaderboard_data,
                'claim': self.guild_claim_data
            }

            with open(self.message_ids_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            self.logger.error(f"Error saving message IDs: {e}")

    async def setup_initial_leaderboard(self, guild_id: int):
        """Setup initial leaderboard and claim messages for a specific guild"""
        try:
            # Get leaderboard channel for this guild
            leaderboard_channel_id = config.get_channel_id(guild_id, 'leaderboard_channel')
            if not leaderboard_channel_id:
                self.logger.warning(f"No leaderboard channel configured for guild {guild_id}")
                return

            channel = self.bot.get_channel(leaderboard_channel_id)
            if not channel:
                self.logger.error(f"Leaderboard channel {leaderboard_channel_id} not found for guild {guild_id}")
                return

            guild_str = str(guild_id)

            # Verify existing message IDs are still valid
            if guild_str in self.guild_leaderboard_data:
                message_id = self.guild_leaderboard_data[guild_str]
                try:
                    await channel.fetch_message(message_id)
                    self.logger.info(f"Found existing leaderboard message {message_id} for guild {guild_id}")
                except discord.NotFound:
                    self.logger.warning(
                        f"Stored leaderboard message {message_id} no longer exists for guild {guild_id}")
                    del self.guild_leaderboard_data[guild_str]

            if guild_str in self.guild_claim_data:
                message_id = self.guild_claim_data[guild_str]
                try:
                    await channel.fetch_message(message_id)
                    self.logger.info(f"Found existing claim message {message_id} for guild {guild_id}")
                except discord.NotFound:
                    self.logger.warning(f"Stored claim message {message_id} no longer exists for guild {guild_id}")
                    del self.guild_claim_data[guild_str]

            # Update leaderboard (will find existing message if ID is None)
            await self.update_leaderboard_now(guild_id)

            # Setup claim message if needed
            if guild_str not in self.guild_claim_data:
                # Try to find existing claim message first
                found_claim = False
                async for msg in channel.history(limit=50):
                    if (msg.author == self.bot.user and
                            msg.embeds and
                            msg.embeds[0].title and
                            "일일 코인" in msg.embeds[0].title):
                        self.guild_claim_data[guild_str] = msg.id
                        await self.save_message_ids()
                        # Ensure the view is attached
                        await msg.edit(view=CoinsView(self.bot))
                        found_claim = True
                        self.logger.info(f"Found and updated existing claim message {msg.id} for guild {guild_id}")
                        break

                # Create new claim message only if none found
                if not found_claim:
                    embed = discord.Embed(
                        title="💰 일일 코인",
                        description="매일 자정(EST)에 초기화됩니다.\n아래 버튼을 클릭하여 일일 코인을 받으세요!",
                        color=discord.Color.green()
                    )
                    message = await channel.send(embed=embed, view=CoinsView(self.bot))
                    self.guild_claim_data[guild_str] = message.id
                    await self.save_message_ids()
                    self.logger.info(f"Created new claim message {message.id} for guild {guild_id}")

            self.logger.info(f"Initial leaderboard setup completed for guild {guild_id}")
        except Exception as e:
            self.logger.error(f"Error in initial leaderboard setup for guild {guild_id}: {e}")

    async def schedule_leaderboard_update(self, guild_id: int):
        """Schedule a delayed leaderboard update to debounce multiple changes"""
        if self.pending_leaderboard_updates.get(guild_id, False):
            return

        self.pending_leaderboard_updates[guild_id] = True

        # Wait for debounce period
        await asyncio.sleep(self.update_delay)

        try:
            await self.update_leaderboard_now(guild_id)
        except Exception as e:
            self.logger.error(f"Error in scheduled leaderboard update for guild {guild_id}: {e}")
        finally:
            self.pending_leaderboard_updates[guild_id] = False

    async def should_update_leaderboard(self, guild_id: int) -> bool:
        """Check if leaderboard actually needs updating by comparing data"""
        try:
            # Get current top 10 for comparison
            query = """
                SELECT user_id, coins 
                FROM user_coins 
                WHERE coins > 0 AND guild_id = $1
                ORDER BY coins DESC 
                LIMIT 10
            """
            current_data = await self.bot.pool.fetch(query, guild_id)

            # Convert to comparable format
            current_top = [(record['user_id'], record['coins']) for record in current_data]

            # Compare with cached data
            if self.last_leaderboard_cache.get(guild_id) == current_top:
                return False

            self.last_leaderboard_cache[guild_id] = current_top
            return True

        except Exception as e:
            self.logger.error(f"Error checking leaderboard changes for guild {guild_id}: {e}")
            return True  # Update on error to be safe

    async def update_leaderboard_now(self, guild_id: int):
        """Update leaderboard immediately using only message edits for specific guild"""
        # Get leaderboard channel for this guild
        leaderboard_channel_id = config.get_channel_id(guild_id, 'leaderboard_channel')
        if not leaderboard_channel_id:
            return

        # Check if update is actually needed
        if not await self.should_update_leaderboard(guild_id):
            return

        try:
            channel = self.bot.get_channel(leaderboard_channel_id)
            if not channel:
                return

            # Create new leaderboard
            leaderboard_view = LeaderboardView(self.bot, guild_id)
            leaderboard_embed = await leaderboard_view.create_leaderboard_embed()

            guild_str = str(guild_id)

            # Try to edit existing message first
            if guild_str in self.guild_leaderboard_data:
                try:
                    message_id = self.guild_leaderboard_data[guild_str]
                    message = await channel.fetch_message(message_id)
                    await message.edit(embed=leaderboard_embed, view=leaderboard_view)
                    self.logger.info(f"Leaderboard updated via edit for guild {guild_id}")
                    return  # Successfully edited, exit early
                except discord.NotFound:
                    self.logger.warning(
                        f"Leaderboard message {message_id} not found for guild {guild_id}, will search for existing message")
                    del self.guild_leaderboard_data[guild_str]  # Reset to search for existing
                except discord.HTTPException as e:
                    # Handle rate limits gracefully
                    if e.status == 429:
                        self.logger.warning(f"Rate limited while updating leaderboard for guild {guild_id}")
                        return  # Skip this update due to rate limit
                    else:
                        self.logger.error(f"HTTP error updating leaderboard for guild {guild_id}: {e}")
                        return

            # If no stored message ID, try to find existing leaderboard message
            async for msg in channel.history(limit=50):
                if (msg.author == self.bot.user and
                        msg.embeds and
                        msg.embeds[0].title and
                        "리더보드" in msg.embeds[0].title):
                    try:
                        await msg.edit(embed=leaderboard_embed, view=leaderboard_view)
                        self.guild_leaderboard_data[guild_str] = msg.id  # Store the found message ID
                        await self.save_message_ids()  # Persist the ID
                        self.logger.info(
                            f"Found and updated existing leaderboard message {msg.id} for guild {guild_id}")
                        return
                    except discord.HTTPException:
                        continue  # Try next message if this one fails

            # Only create new message if we absolutely cannot find or edit an existing one
            message = await channel.send(embed=leaderboard_embed, view=leaderboard_view)
            self.guild_leaderboard_data[guild_str] = message.id
            await self.save_message_ids()  # Persist the new ID
            self.logger.info(
                f"Created new leaderboard message {message.id} for guild {guild_id} (no existing message found)")

        except Exception as e:
            self.logger.error(f"Error updating leaderboard for guild {guild_id}: {e}")

    async def setup_database(self):
        """Create necessary database tables with indexes for better performance"""
        try:
            await self.bot.pool.execute("""
                CREATE TABLE IF NOT EXISTS user_coins (
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    coins INTEGER DEFAULT 0,
                    last_claim_date TIMESTAMP,
                    total_earned INTEGER DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)

            await self.bot.pool.execute("""
                CREATE TABLE IF NOT EXISTS coin_transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    amount INTEGER NOT NULL,
                    transaction_type VARCHAR(50) NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for better performance
            await self.bot.pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_coins_guild_coins ON user_coins(guild_id, coins DESC);
            """)

            await self.bot.pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_coin_transactions_user_guild ON coin_transactions(user_id, guild_id);
            """)

            await self.bot.pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_coin_transactions_guild_type ON coin_transactions(guild_id, transaction_type);
            """)

            self.logger.info("✅ 코인 데이터베이스 테이블이 준비되었습니다.")
        except Exception as e:
            self.logger.error(f"❌ 데이터베이스 설정 실패: {e}")

    def check_spam_protection(self, user_id: int) -> bool:
        """Check if user is spamming commands"""
        now = datetime.now()
        if user_id in self.last_command_time:
            time_diff = (now - self.last_command_time[user_id]).total_seconds()
            if time_diff < self.cooldown_seconds:
                return False

        self.last_command_time[user_id] = now
        return True

    async def get_user_coins(self, user_id: int, guild_id: int) -> int:
        """Get user's current coin balance for specific guild"""
        try:
            row = await self.bot.pool.fetchrow(
                "SELECT coins FROM user_coins WHERE user_id = $1 AND guild_id = $2",
                user_id, guild_id
            )
            return row['coins'] if row else 0
        except Exception as e:
            self.logger.error(f"Error getting coins for {user_id} in guild {guild_id}: {e}")
            return 0

    async def add_coins(self, user_id: int, guild_id: int, amount: int, transaction_type: str = "earned",
                        description: str = ""):
        """Add coins to user account and trigger leaderboard update"""
        try:
            # Update user coins
            await self.bot.pool.execute("""
                INSERT INTO user_coins (user_id, guild_id, coins, total_earned)
                VALUES ($1, $2, $3, $3)
                ON CONFLICT (user_id, guild_id) 
                DO UPDATE SET 
                    coins = user_coins.coins + $3,
                    total_earned = user_coins.total_earned + $3
            """, user_id, guild_id, amount)

            # Log transaction
            await self.bot.pool.execute("""
                INSERT INTO coin_transactions (user_id, guild_id, amount, transaction_type, description)
                VALUES ($1, $2, $3, $4, $5)
            """, user_id, guild_id, amount, transaction_type, description)

            # Trigger real-time leaderboard update
            self.bot.loop.create_task(self.schedule_leaderboard_update(guild_id))

            self.logger.info(f"Added {amount} coins to user {user_id} in guild {guild_id}: {description}")
            return True
        except Exception as e:
            self.logger.error(f"Error adding coins to {user_id} in guild {guild_id}: {e}")
            return False

    async def remove_coins(self, user_id: int, guild_id: int, amount: int, transaction_type: str = "spent",
                           description: str = "") -> bool:
        """Remove coins from user account and trigger leaderboard update"""
        try:
            current_coins = await self.get_user_coins(user_id, guild_id)
            if current_coins < amount:
                return False

            # Update user coins
            await self.bot.pool.execute("""
                UPDATE user_coins 
                SET coins = coins - $3, total_spent = total_spent + $3
                WHERE user_id = $1 AND guild_id = $2
            """, user_id, guild_id, amount)

            # Log transaction
            await self.bot.pool.execute("""
                INSERT INTO coin_transactions (user_id, guild_id, amount, transaction_type, description)
                VALUES ($1, $2, $3, $4, $5)
            """, user_id, guild_id, -amount, transaction_type, description)

            # Trigger real-time leaderboard update
            self.bot.loop.create_task(self.schedule_leaderboard_update(guild_id))

            self.logger.info(f"Removed {amount} coins from user {user_id} in guild {guild_id}: {description}")
            return True
        except Exception as e:
            self.logger.error(f"Error removing coins from {user_id} in guild {guild_id}: {e}")
            return False

    # Keep the original scheduled task as a backup/maintenance function
    @tasks.loop(hours=1)  # Reduced frequency since we have real-time updates
    async def maintenance_leaderboard_update(self):
        """Maintenance update every hour to ensure consistency for all guilds"""
        try:
            all_configs = config.get_all_server_configs()
            for guild_id_str, guild_config in all_configs.items():
                if guild_config.get('features', {}).get('casino_games'):
                    guild_id = int(guild_id_str)

                    # Get leaderboard channel for this guild
                    leaderboard_channel_id = config.get_channel_id(guild_id, 'leaderboard_channel')
                    if not leaderboard_channel_id:
                        continue

                    # Force update to ensure consistency
                    if guild_id in self.last_leaderboard_cache:
                        del self.last_leaderboard_cache[guild_id]
                    await self.update_leaderboard_now(guild_id)

                    # Also check if claim message needs maintenance
                    channel = self.bot.get_channel(leaderboard_channel_id)
                    if channel:
                        guild_str = str(guild_id)
                        if guild_str in self.guild_claim_data:
                            try:
                                message_id = self.guild_claim_data[guild_str]
                                message = await channel.fetch_message(message_id)
                                if not message.components:  # Re-add view if missing
                                    await message.edit(view=CoinsView(self.bot))
                            except discord.NotFound:
                                # Recreate claim message if missing
                                embed = discord.Embed(
                                    title="💰 일일 코인",
                                    description="매일 자정(EST)에 초기화됩니다.\n아래 버튼을 클릭하여 일일 코인을 받으세요!",
                                    color=discord.Color.green()
                                )
                                message = await channel.send(embed=embed, view=CoinsView(self.bot))
                                self.guild_claim_data[guild_str] = message.id
                                await self.save_message_ids()

        except Exception as e:
            self.logger.error(f"Error in maintenance leaderboard update: {e}")

    @app_commands.command(name="코인", description="현재 코인 잔액을 확인합니다.")
    async def check_coins(self, interaction: discord.Interaction, user: discord.Member = None):
        # Check if casino games are enabled
        if not config.is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message(
                "❌ 이 서버에서는 코인 시스템이 비활성화되어 있습니다.",
                ephemeral=True
            )
            return

        if not self.check_spam_protection(interaction.user.id):
            await interaction.response.send_message("⏳ 잠시만 기다려주세요!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        target_user = user or interaction.user
        guild_id = interaction.guild.id
        coins = await self.get_user_coins(target_user.id, guild_id)

        try:
            # Get additional stats
            stats_query = """
                SELECT total_earned, total_spent, last_claim_date
                FROM user_coins WHERE user_id = $1 AND guild_id = $2
            """
            stats = await self.bot.pool.fetchrow(stats_query, target_user.id, guild_id)

            embed = discord.Embed(
                title=f"💰 {target_user.display_name}님의 코인 정보",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )

            embed.add_field(name="현재 잔액", value=f"{coins:,} 코인", inline=True)

            if stats:
                embed.add_field(name="총 획득", value=f"{stats['total_earned'] or 0:,} 코인", inline=True)
                embed.add_field(name="총 사용", value=f"{stats['total_spent'] or 0:,} 코인", inline=True)

                if stats['last_claim_date']:
                    last_claim = stats['last_claim_date'].strftime("%Y-%m-%d %H:%M")
                    embed.add_field(name="마지막 일일 코인", value=last_claim, inline=False)

            embed.set_thumbnail(url=target_user.display_avatar.url)

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
            self.logger.error(f"Error in check_coins: {e}")

    @app_commands.command(name="코인주기", description="다른 사용자에게 코인을 전송합니다.")
    @app_commands.describe(
        user="코인을 받을 사용자",
        amount="전송할 코인 수량"
    )
    async def transfer_coins(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        # Check if casino games are enabled
        if not config.is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message(
                "❌ 이 서버에서는 코인 시스템이 비활성화되어 있습니다.",
                ephemeral=True
            )
            return

        if not self.check_spam_protection(interaction.user.id):
            await interaction.response.send_message("⏳ 잠시만 기다려주세요!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if user.bot:
            await interaction.followup.send("❌ 봇에게는 코인을 전송할 수 없습니다.", ephemeral=True)
            return

        if user.id == interaction.user.id:
            await interaction.followup.send("❌ 자기 자신에게는 코인을 전송할 수 없습니다.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.followup.send("❌ 1 코인 이상만 전송 가능합니다.", ephemeral=True)
            return

        if amount > 1000:
            await interaction.followup.send("❌ 한 번에 최대 1,000 코인까지만 전송 가능합니다.", ephemeral=True)
            return

        guild_id = interaction.guild.id

        # Check if sender has enough coins
        sender_coins = await self.get_user_coins(interaction.user.id, guild_id)
        if sender_coins < amount:
            await interaction.followup.send(f"❌ 코인이 부족합니다. 현재 잔액: {sender_coins} 코인", ephemeral=True)
            return

        try:
            # Transfer coins
            success = await self.remove_coins(
                interaction.user.id,
                guild_id,
                amount,
                "transfer_sent",
                f"Transferred to {user.display_name}"
            )

            if success:
                await self.add_coins(
                    user.id,
                    guild_id,
                    amount,
                    "transfer_received",
                    f"Received from {interaction.user.display_name}"
                )

                embed = discord.Embed(
                    title="✅ 코인 전송 완료",
                    description=f"{amount} 코인을 {user.mention}님에게 전송했습니다.",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc)
                )

                await interaction.followup.send(embed=embed, ephemeral=True)
                self.logger.info(f"{interaction.user} transferred {amount} coins to {user} in guild {guild_id}")

            else:
                await interaction.followup.send("❌ 전송에 실패했습니다.", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
            self.logger.error(f"Error in transfer_coins: {e}")

    # Admin commands
    @app_commands.command(name="코인관리", description="[관리자] 사용자의 코인을 관리합니다.")
    @app_commands.describe(
        user="대상 사용자",
        action="수행할 작업",
        amount="코인 수량"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="추가", value="add"),
        app_commands.Choice(name="제거", value="remove"),
        app_commands.Choice(name="설정", value="set")
    ])
    async def admin_manage_coins(self, interaction: discord.Interaction, user: discord.Member, action: str,
                                 amount: int):
        # Check if casino games are enabled
        if not config.is_feature_enabled(interaction.guild.id, 'casino_games'):
            await interaction.response.send_message(
                "❌ 이 서버에서는 코인 시스템이 비활성화되어 있습니다.",
                ephemeral=True
            )
            return

        # Check if user has admin permissions
        if not self.has_admin_permissions(interaction.user):
            await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild.id

        try:
            if action == "add":
                await self.add_coins(user.id, guild_id, amount, "admin_add",
                                     f"Admin {interaction.user.display_name} added coins")
                result_text = f"{amount} 코인을 추가했습니다."

            elif action == "remove":
                success = await self.remove_coins(user.id, guild_id, amount, "admin_remove",
                                                  f"Admin {interaction.user.display_name} removed coins")
                if not success:
                    await interaction.followup.send("❌ 코인이 부족하여 제거할 수 없습니다.", ephemeral=True)
                    return
                result_text = f"{amount} 코인을 제거했습니다."

            elif action == "set":
                current_coins = await self.get_user_coins(user.id, guild_id)
                difference = amount - current_coins

                if difference > 0:
                    await self.add_coins(user.id, guild_id, difference, "admin_set",
                                         f"Admin {interaction.user.display_name} set coins")
                elif difference < 0:
                    await self.remove_coins(user.id, guild_id, abs(difference), "admin_set",
                                            f"Admin {interaction.user.display_name} set coins")

                result_text = f"코인을 {amount}개로 설정했습니다."

            new_balance = await self.get_user_coins(user.id, guild_id)

            embed = discord.Embed(
                title="✅ 코인 관리 완료",
                description=f"{user.mention}님의 코인을 관리했습니다.\n{result_text}\n현재 잔액: {new_balance:,} 코인",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.info(
                f"Admin {interaction.user} managed coins for {user} in guild {guild_id}: {action} {amount}")

        except Exception as e:
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
            self.logger.error(f"Error in admin_manage_coins: {e}")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Handle new guild joins - setup coins system if enabled"""
        # Check if the guild gets configured with casino games
        # This will be called later when setup is run, but we can prepare
        self.logger.info(f"Bot joined new guild: {guild.name} ({guild.id})")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Handle guild removal - cleanup guild-specific data"""
        guild_str = str(guild.id)

        # Clean up cached data
        if guild_str in self.guild_leaderboard_data:
            del self.guild_leaderboard_data[guild_str]
        if guild_str in self.guild_claim_data:
            del self.guild_claim_data[guild_str]
        if guild.id in self.last_leaderboard_cache:
            del self.last_leaderboard_cache[guild.id]
        if guild.id in self.pending_leaderboard_updates:
            del self.pending_leaderboard_updates[guild.id]

        await self.save_message_ids()
        self.logger.info(f"Cleaned up coins data for removed guild: {guild.name} ({guild.id})")

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        if hasattr(self, 'maintenance_leaderboard_update') and self.maintenance_leaderboard_update.is_running():
            self.maintenance_leaderboard_update.cancel()
        self.logger.info("코인 시스템이 언로드되었습니다.")


async def setup(bot):
    await bot.add_cog(CoinsCog(bot))