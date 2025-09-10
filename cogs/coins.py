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
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        eastern = pytz.timezone('America/New_York')
        now = datetime.now(eastern)  # This is timezone-aware
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)  # Still timezone-aware

        try:
            # Check if user already claimed today
            check_query = """
                    SELECT last_claim_date FROM user_coins 
                    WHERE user_id = $1
                """
            row = await self.bot.pool.fetchrow(check_query, user_id)

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

            # Give daily coins using the add_coins method to trigger leaderboard update
            coins_cog = self.bot.get_cog('CoinsCog')
            if coins_cog:
                # Store as naive datetime in the database (convert timezone-aware to naive)
                naive_now = now.replace(tzinfo=None)

                # Update the database directly for daily claims to include last_claim_date
                update_query = """
                        INSERT INTO user_coins (user_id, coins, last_claim_date, total_earned)
                        VALUES ($1, 10, $2, 10)
                        ON CONFLICT (user_id) 
                        DO UPDATE SET 
                            coins = user_coins.coins + 10,
                            total_earned = user_coins.total_earned + 10,
                            last_claim_date = EXCLUDED.last_claim_date
                        RETURNING coins
                    """
                result = await self.bot.pool.fetchrow(update_query, user_id, naive_now)

                # Log transaction
                await self.bot.pool.execute("""
                        INSERT INTO coin_transactions (user_id, amount, transaction_type, description)
                        VALUES ($1, $2, $3, $4)
                    """, user_id, 50, "daily_claim", "Daily coin claim")

                # Trigger leaderboard update
                self.bot.loop.create_task(coins_cog.schedule_leaderboard_update())

                embed = discord.Embed(
                    title="💰 일일 코인 지급!",
                    description=f"✅ 50 코인을 받았습니다!\n현재 잔액: **{result['coins']} 코인**",
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.set_footer(text="다음 받기는 내일 자정(EST)에 가능합니다")

                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
            self.bot.logger.error(f"Daily coin claim error for {user_id}: {e}")
class LeaderboardView(discord.ui.View):
    """Persistent view for coin leaderboard navigation"""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.current_page = 0
        self.users_per_page = 10

    async def get_leaderboard_data(self):
        """Get leaderboard data from database"""
        query = """
            SELECT user_id, coins 
            FROM user_coins 
            WHERE coins > 0 
            ORDER BY coins DESC 
            LIMIT 100
        """
        return await self.bot.pool.fetch(query)

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
        self.logger = get_logger(
            "코인 시스템",
            bot=self.bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        # Spam protection - user_id: last_command_time
        self.last_command_time = {}
        self.cooldown_seconds = 3

        # Channel IDs - SET THESE MANUALLY
        self.LEADERBOARD_CHANNEL_ID = 1415180123010760806
        self.leaderboard_message_id = None
        self.claim_message_id = None

        # Real-time update controls
        self.pending_leaderboard_update = False
        self.update_delay = 3  # seconds to debounce updates
        self.last_leaderboard_data = None  # Cache to avoid unnecessary updates

        # Message ID persistence
        self.message_ids_file = "data/message_ids.json"

        self.logger.info("코인 시스템이 초기화되었습니다.")

        # Start tasks after bot is ready
        self.bot.loop.create_task(self.wait_and_start_tasks())

    async def wait_and_start_tasks(self):
        """Wait for bot to be ready then start tasks"""
        await self.bot.wait_until_ready()
        await self.setup_database()
        await self.load_message_ids()  # Load persistent message IDs
        # Initial leaderboard setup
        if self.LEADERBOARD_CHANNEL_ID:
            await self.setup_initial_leaderboard()
            # Start maintenance task instead of frequent updates
            self.maintenance_leaderboard_update.start()

    async def load_message_ids(self):
        """Load persistent message IDs from file"""
        try:
            if os.path.exists(self.message_ids_file):
                with open(self.message_ids_file, 'r') as f:
                    data = json.load(f)
                    self.leaderboard_message_id = data.get('leaderboard_message_id')
                    self.claim_message_id = data.get('claim_message_id')
                    self.logger.info(
                        f"Loaded message IDs: leaderboard={self.leaderboard_message_id}, claim={self.claim_message_id}")
        except Exception as e:
            self.logger.error(f"Error loading message IDs: {e}")

    async def save_message_ids(self):
        """Save message IDs to file for persistence"""
        try:
            os.makedirs(os.path.dirname(self.message_ids_file), exist_ok=True)

            data = {
                'leaderboard_message_id': self.leaderboard_message_id,
                'claim_message_id': self.claim_message_id
            }

            with open(self.message_ids_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            self.logger.error(f"Error saving message IDs: {e}")

    async def setup_initial_leaderboard(self):
        """Setup initial leaderboard and claim messages using existing messages when possible"""
        try:
            channel = self.bot.get_channel(self.LEADERBOARD_CHANNEL_ID)
            if not channel:
                self.logger.error(f"Leaderboard channel {self.LEADERBOARD_CHANNEL_ID} not found")
                return

            # Verify existing message IDs are still valid
            if self.leaderboard_message_id:
                try:
                    await channel.fetch_message(self.leaderboard_message_id)
                    self.logger.info(f"Found existing leaderboard message {self.leaderboard_message_id}")
                except discord.NotFound:
                    self.logger.warning(f"Stored leaderboard message {self.leaderboard_message_id} no longer exists")
                    self.leaderboard_message_id = None

            if self.claim_message_id:
                try:
                    await channel.fetch_message(self.claim_message_id)
                    self.logger.info(f"Found existing claim message {self.claim_message_id}")
                except discord.NotFound:
                    self.logger.warning(f"Stored claim message {self.claim_message_id} no longer exists")
                    self.claim_message_id = None

            # Update leaderboard (will find existing message if ID is None)
            await self.update_leaderboard_now()

            # Setup claim message if needed
            if not self.claim_message_id:
                # Try to find existing claim message first
                found_claim = False
                async for msg in channel.history(limit=50):
                    if (msg.author == self.bot.user and
                            msg.embeds and
                            msg.embeds[0].title and
                            "일일 코인" in msg.embeds[0].title):
                        self.claim_message_id = msg.id
                        await self.save_message_ids()
                        # Ensure the view is attached
                        await msg.edit(view=CoinsView(self.bot))
                        found_claim = True
                        self.logger.info(f"Found and updated existing claim message {msg.id}")
                        break

                # Create new claim message only if none found
                if not found_claim:
                    embed = discord.Embed(
                        title="💰 일일 코인",
                        description="매일 자정(EST)에 초기화됩니다.\n아래 버튼을 클릭하여 일일 코인을 받으세요!",
                        color=discord.Color.green()
                    )
                    message = await channel.send(embed=embed, view=CoinsView(self.bot))
                    self.claim_message_id = message.id
                    await self.save_message_ids()
                    self.logger.info(f"Created new claim message {message.id}")

            self.logger.info("Initial leaderboard setup completed")
        except Exception as e:
            self.logger.error(f"Error in initial leaderboard setup: {e}")

    async def schedule_leaderboard_update(self):
        """Schedule a delayed leaderboard update to debounce multiple changes"""
        if self.pending_leaderboard_update:
            return

        self.pending_leaderboard_update = True

        # Wait for debounce period
        await asyncio.sleep(self.update_delay)

        try:
            await self.update_leaderboard_now()
        except Exception as e:
            self.logger.error(f"Error in scheduled leaderboard update: {e}")
        finally:
            self.pending_leaderboard_update = False

    async def should_update_leaderboard(self) -> bool:
        """Check if leaderboard actually needs updating by comparing data"""
        try:
            # Get current top 10 for comparison
            query = """
                SELECT user_id, coins 
                FROM user_coins 
                WHERE coins > 0 
                ORDER BY coins DESC 
                LIMIT 10
            """
            current_data = await self.bot.pool.fetch(query)

            # Convert to comparable format
            current_top = [(record['user_id'], record['coins']) for record in current_data]

            # Compare with cached data
            if self.last_leaderboard_data == current_top:
                return False

            self.last_leaderboard_data = current_top
            return True

        except Exception as e:
            self.logger.error(f"Error checking leaderboard changes: {e}")
            return True  # Update on error to be safe

    async def update_leaderboard_now(self):
        """Update leaderboard immediately using only message edits"""
        if not self.LEADERBOARD_CHANNEL_ID:
            return

        # Check if update is actually needed
        if not await self.should_update_leaderboard():
            return

        try:
            channel = self.bot.get_channel(self.LEADERBOARD_CHANNEL_ID)
            if not channel:
                return

            # Create new leaderboard
            leaderboard_view = LeaderboardView(self.bot)
            leaderboard_embed = await leaderboard_view.create_leaderboard_embed()

            # Try to edit existing message first
            if self.leaderboard_message_id:
                try:
                    message = await channel.fetch_message(self.leaderboard_message_id)
                    await message.edit(embed=leaderboard_embed, view=leaderboard_view)
                    self.logger.info("Leaderboard updated via edit")
                    return  # Successfully edited, exit early
                except discord.NotFound:
                    self.logger.warning(
                        f"Leaderboard message {self.leaderboard_message_id} not found, will search for existing message")
                    self.leaderboard_message_id = None  # Reset to search for existing
                except discord.HTTPException as e:
                    # Handle rate limits gracefully
                    if e.status == 429:
                        self.logger.warning("Rate limited while updating leaderboard")
                        return  # Skip this update due to rate limit
                    else:
                        self.logger.error(f"HTTP error updating leaderboard: {e}")
                        return

            # If no stored message ID, try to find existing leaderboard message
            # Look for the most recent message from the bot with leaderboard title
            async for msg in channel.history(limit=50):
                if (msg.author == self.bot.user and
                        msg.embeds and
                        msg.embeds[0].title and
                        "리더보드" in msg.embeds[0].title):
                    try:
                        await msg.edit(embed=leaderboard_embed, view=leaderboard_view)
                        self.leaderboard_message_id = msg.id  # Store the found message ID
                        await self.save_message_ids()  # Persist the ID
                        self.logger.info(f"Found and updated existing leaderboard message {msg.id}")
                        return
                    except discord.HTTPException:
                        continue  # Try next message if this one fails

            # Only create new message if we absolutely cannot find or edit an existing one
            message = await channel.send(embed=leaderboard_embed, view=leaderboard_view)
            self.leaderboard_message_id = message.id
            await self.save_message_ids()  # Persist the new ID
            self.logger.info(f"Created new leaderboard message {message.id} (no existing message found)")

        except Exception as e:
            self.logger.error(f"Error updating leaderboard: {e}")

    async def setup_database(self):
        """Create necessary database tables with indexes for better performance"""
        try:
            await self.bot.pool.execute("""
                CREATE TABLE IF NOT EXISTS user_coins (
                    user_id BIGINT PRIMARY KEY,
                    coins INTEGER DEFAULT 0,
                    last_claim_date TIMESTAMP,
                    total_earned INTEGER DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await self.bot.pool.execute("""
                CREATE TABLE IF NOT EXISTS coin_transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    amount INTEGER NOT NULL,
                    transaction_type VARCHAR(50) NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes for better performance
            await self.bot.pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_coins_coins ON user_coins(coins DESC);
            """)

            await self.bot.pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_coin_transactions_user_id ON coin_transactions(user_id);
            """)

            await self.bot.pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_coin_transactions_type ON coin_transactions(transaction_type);
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

    async def get_user_coins(self, user_id: int) -> int:
        """Get user's current coin balance"""
        try:
            row = await self.bot.pool.fetchrow(
                "SELECT coins FROM user_coins WHERE user_id = $1", user_id
            )
            return row['coins'] if row else 0
        except Exception as e:
            self.logger.error(f"Error getting coins for {user_id}: {e}")
            return 0

    async def add_coins(self, user_id: int, amount: int, transaction_type: str = "earned", description: str = ""):
        """Add coins to user account and trigger leaderboard update"""
        try:
            # Update user coins
            await self.bot.pool.execute("""
                INSERT INTO user_coins (user_id, coins, total_earned)
                VALUES ($1, $2, $2)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    coins = user_coins.coins + $2,
                    total_earned = user_coins.total_earned + $2
            """, user_id, amount)

            # Log transaction
            await self.bot.pool.execute("""
                INSERT INTO coin_transactions (user_id, amount, transaction_type, description)
                VALUES ($1, $2, $3, $4)
            """, user_id, amount, transaction_type, description)

            # Trigger real-time leaderboard update
            self.bot.loop.create_task(self.schedule_leaderboard_update())

            self.logger.info(f"Added {amount} coins to user {user_id}: {description}")
            return True
        except Exception as e:
            self.logger.error(f"Error adding coins to {user_id}: {e}")
            return False

    async def remove_coins(self, user_id: int, amount: int, transaction_type: str = "spent",
                           description: str = "") -> bool:
        """Remove coins from user account and trigger leaderboard update"""
        try:
            current_coins = await self.get_user_coins(user_id)
            if current_coins < amount:
                return False

            # Update user coins
            await self.bot.pool.execute("""
                UPDATE user_coins 
                SET coins = coins - $2, total_spent = total_spent + $2
                WHERE user_id = $1
            """, user_id, amount)

            # Log transaction
            await self.bot.pool.execute("""
                INSERT INTO coin_transactions (user_id, amount, transaction_type, description)
                VALUES ($1, $2, $3, $4)
            """, user_id, -amount, transaction_type, description)

            # Trigger real-time leaderboard update
            self.bot.loop.create_task(self.schedule_leaderboard_update())

            self.logger.info(f"Removed {amount} coins from user {user_id}: {description}")
            return True
        except Exception as e:
            self.logger.error(f"Error removing coins from {user_id}: {e}")
            return False

    # Keep the original scheduled task as a backup/maintenance function
    @tasks.loop(hours=1)  # Reduced frequency since we have real-time updates
    async def maintenance_leaderboard_update(self):
        """Maintenance update every hour to ensure consistency"""
        if not self.LEADERBOARD_CHANNEL_ID:
            return

        try:
            # Force update to ensure consistency
            self.last_leaderboard_data = None
            await self.update_leaderboard_now()

            # Also check if claim message needs maintenance
            channel = self.bot.get_channel(self.LEADERBOARD_CHANNEL_ID)
            if channel and self.claim_message_id:
                try:
                    message = await channel.fetch_message(self.claim_message_id)
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
                    self.claim_message_id = message.id
                    await self.save_message_ids()

        except Exception as e:
            self.logger.error(f"Error in maintenance leaderboard update: {e}")

    @app_commands.command(name="코인", description="현재 코인 잔액을 확인합니다.")
    async def check_coins(self, interaction: discord.Interaction, user: discord.Member = None):
        if not self.check_spam_protection(interaction.user.id):
            await interaction.response.send_message("⏳ 잠시만 기다려주세요!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        target_user = user or interaction.user
        coins = await self.get_user_coins(target_user.id)

        try:
            # Get additional stats
            stats_query = """
                SELECT total_earned, total_spent, last_claim_date
                FROM user_coins WHERE user_id = $1
            """
            stats = await self.bot.pool.fetchrow(stats_query, target_user.id)

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

        # Check if sender has enough coins
        sender_coins = await self.get_user_coins(interaction.user.id)
        if sender_coins < amount:
            await interaction.followup.send(f"❌ 코인이 부족합니다. 현재 잔액: {sender_coins} 코인", ephemeral=True)
            return

        try:
            # Transfer coins
            success = await self.remove_coins(
                interaction.user.id,
                amount,
                "transfer_sent",
                f"Transferred to {user.display_name}"
            )

            if success:
                await self.add_coins(
                    user.id,
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
                self.logger.info(f"{interaction.user} transferred {amount} coins to {user}")

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
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            if action == "add":
                await self.add_coins(user.id, amount, "admin_add", f"Admin {interaction.user.display_name} added coins")
                result_text = f"{amount} 코인을 추가했습니다."

            elif action == "remove":
                success = await self.remove_coins(user.id, amount, "admin_remove",
                                                  f"Admin {interaction.user.display_name} removed coins")
                if not success:
                    await interaction.followup.send("❌ 코인이 부족하여 제거할 수 없습니다.", ephemeral=True)
                    return
                result_text = f"{amount} 코인을 제거했습니다."

            elif action == "set":
                current_coins = await self.get_user_coins(user.id)
                difference = amount - current_coins

                if difference > 0:
                    await self.add_coins(user.id, difference, "admin_set",
                                         f"Admin {interaction.user.display_name} set coins")
                elif difference < 0:
                    await self.remove_coins(user.id, abs(difference), "admin_set",
                                            f"Admin {interaction.user.display_name} set coins")

                result_text = f"코인을 {amount}개로 설정했습니다."

            new_balance = await self.get_user_coins(user.id)

            embed = discord.Embed(
                title="✅ 코인 관리 완료",
                description=f"{user.mention}님의 코인을 관리했습니다.\n{result_text}\n현재 잔액: {new_balance:,} 코인",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.info(f"Admin {interaction.user} managed coins for {user}: {action} {amount}")

        except Exception as e:
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)
            self.logger.error(f"Error in admin_manage_coins: {e}")

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        if hasattr(self, 'maintenance_leaderboard_update') and self.maintenance_leaderboard_update.is_running():
            self.maintenance_leaderboard_update.cancel()
        self.logger.info("코인 시스템이 언로드되었습니다.")


async def setup(bot):
    await bot.add_cog(CoinsCog(bot))