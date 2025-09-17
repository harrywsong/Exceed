# cogs/registration.py - Updated for multi-server support
import discord
from discord import app_commands
from discord.ext import commands
from utils.logger import get_logger
from utils.config import (
    get_channel_id,
    is_feature_enabled,
    is_server_configured
)


class Registration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger("등록 기능")
        self.logger.info("등록 기능이 초기화되었습니다.")

    async def setup_database(self):
        """Create necessary database tables for multi-server support"""
        try:
            await self.bot.pool.execute("""
                CREATE TABLE IF NOT EXISTS registrations (
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    riot_id VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)

            # Create index for better performance
            await self.bot.pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_registrations_guild_riot 
                ON registrations(guild_id, riot_id);
            """)

            # 이 로그는 봇 전체에 대한 것이므로 guild_id가 필요하지 않습니다.
            self.logger.info("✅ 등록 데이터베이스 테이블이 준비되었습니다.")
        except Exception as e:
            # 이 로그도 봇 전체에 대한 것이므로 guild_id가 필요하지 않습니다.
            self.logger.error(f"❌ 등록 데이터베이스 설정 실패: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Setup database when bot is ready"""
        await self.setup_database()

    @app_commands.command(
        name="연동",
        description="디스코드 계정을 라이엇 ID와 연결합니다 (예: Name#Tag)."
    )
    @app_commands.describe(
        riot_id="라이엇 ID (예: winter#겨울밤)"
    )
    async def register(self, interaction: discord.Interaction, riot_id: str):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용 가능한 기능입니다.", ephemeral=True)
            return

        guild_id = interaction.guild.id # 길드 ID를 변수에 저장

        if not is_server_configured(guild_id):
            await interaction.response.send_message("❌ 이 서버는 아직 설정되지 않았습니다. 관리자에게 문의해주세요.", ephemeral=True)
            return

        if not is_feature_enabled(guild_id, 'registration'):
            await interaction.response.send_message("❌ 이 서버에서는 계정 연동 기능이 비활성화되어 있습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        if "#" not in riot_id:
            await interaction.followup.send(
                "❌ 올바르지 않은 형식입니다. `이름#태그` 형태로 입력해주세요.", ephemeral=True
            )
            # extra={'guild_id': guild_id} 추가
            self.logger.warning(
                f"{interaction.user} failed to register with invalid Riot ID format: {riot_id} (서버: {interaction.guild.name})", extra={'guild_id': guild_id})
            return

        discord_id = interaction.user.id
        # guild_id 변수 사용

        try:
            # Check if this Riot ID is already registered by another user in this server
            existing_query = """
                SELECT user_id FROM registrations 
                WHERE guild_id = $1 AND riot_id = $2 AND user_id != $3
            """
            existing_user = await self.bot.pool.fetchrow(existing_query, guild_id, riot_id, discord_id)

            if existing_user:
                existing_member = interaction.guild.get_member(existing_user['user_id'])
                existing_name = existing_member.display_name if existing_member else f"Unknown User ({existing_user['user_id']})"
                await interaction.followup.send(
                    f"❌ 이 라이엇 ID는 이미 **{existing_name}**님이 사용하고 있습니다.", ephemeral=True
                )
                return

            query = """
                INSERT INTO registrations (user_id, guild_id, riot_id, updated_at)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, guild_id) 
                DO UPDATE SET 
                    riot_id = EXCLUDED.riot_id,
                    updated_at = CURRENT_TIMESTAMP
            """
            await self.bot.pool.execute(query, discord_id, guild_id, riot_id)

            await interaction.followup.send(
                f"✅ 라이엇 ID `{riot_id}`와 성공적으로 연결되었습니다!", ephemeral=True
            )

            # Log to server's log channel if configured
            log_channel_id = get_channel_id(guild_id, 'log_channel')
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    try:
                        log_embed = discord.Embed(
                            title="🔗 계정 연동",
                            description=f"{interaction.user.mention}님이 라이엇 ID를 연동했습니다.",
                            color=discord.Color.green(),
                            timestamp=interaction.created_at
                        )
                        log_embed.add_field(name="라이엇 ID", value=riot_id, inline=True)
                        log_embed.add_field(name="사용자", value=f"{interaction.user} ({interaction.user.id})",
                                            inline=True)
                        log_embed.set_thumbnail(url=interaction.user.display_avatar.url)
                        await log_channel.send(embed=log_embed)
                    except Exception as e:
                        # Log channel send failure does not need guild_id in extra if it's a general error
                        self.logger.error(f"Failed to send log message: {e}")

            # extra={'guild_id': guild_id} 추가
            self.logger.info(f"✅ {interaction.user} linked Riot ID: {riot_id} (서버: {interaction.guild.name})", extra={'guild_id': guild_id})

        except Exception as e:
            # extra={'guild_id': guild_id} 추가
            self.logger.error(
                f"❌ Database error during registration for {interaction.user} (서버: {interaction.guild.name}): {e}", extra={'guild_id': guild_id})
            await interaction.followup.send(
                f"❌ 데이터베이스 오류가 발생했습니다: `{e}`", ephemeral=True
            )

    @app_commands.command(
        name="myriot",
        description="등록한 라이엇 ID를 확인합니다."
    )
    async def myriot(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용 가능한 기능입니다.", ephemeral=True)
            return

        guild_id = interaction.guild.id # 길드 ID를 변수에 저장

        if not is_server_configured(guild_id):
            await interaction.response.send_message("❌ 이 서버는 아직 설정되지 않았습니다. 관리자에게 문의해주세요.", ephemeral=True)
            return

        if not is_feature_enabled(guild_id, 'registration'):
            await interaction.response.send_message("❌ 이 서버에서는 계정 연동 기능이 비활성화되어 있습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        discord_id = interaction.user.id
        # guild_id 변수 사용

        try:
            query = "SELECT riot_id, created_at, updated_at FROM registrations WHERE user_id = $1 AND guild_id = $2"
            row = await self.bot.pool.fetchrow(query, discord_id, guild_id)

            if row and row["riot_id"]:
                embed = discord.Embed(
                    title="🔎 등록된 라이엇 ID",
                    color=discord.Color.blue(),
                    timestamp=interaction.created_at
                )
                embed.add_field(name="라이엇 ID", value=f"`{row['riot_id']}`", inline=False)

                if row['created_at']:
                    embed.add_field(name="등록일", value=f"<t:{int(row['created_at'].timestamp())}:F>", inline=True)

                if row['updated_at'] and row['updated_at'] != row['created_at']:
                    embed.add_field(name="마지막 수정", value=f"<t:{int(row['updated_at'].timestamp())}:F>", inline=True)

                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                embed.set_footer(text=f"서버: {interaction.guild.name}")

                await interaction.followup.send(embed=embed, ephemeral=True)
                # extra={'guild_id': guild_id} 추가
                self.logger.info(f"{interaction.user} checked Riot ID: {row['riot_id']} (서버: {interaction.guild.name})", extra={'guild_id': guild_id})
            else:
                embed = discord.Embed(
                    title="❌ 등록된 라이엇 ID가 없습니다",
                    description="아직 라이엇 ID를 등록하지 않았습니다.\n`/연동` 명령어로 등록해주세요.",
                    color=discord.Color.red()
                )
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                await interaction.followup.send(embed=embed, ephemeral=True)
                # extra={'guild_id': guild_id} 추가
                self.logger.info(
                    f"{interaction.user} tried to check Riot ID but none was found. (서버: {interaction.guild.name})", extra={'guild_id': guild_id})

        except Exception as e:
            # extra={'guild_id': guild_id} 추가
            self.logger.error(
                f"❌ Database error during myriot check for {interaction.user} (서버: {interaction.guild.name}): {e}", extra={'guild_id': guild_id})
            await interaction.followup.send(
                f"❌ 데이터베이스 오류가 발생했습니다: `{e}`", ephemeral=True
            )

    @app_commands.command(
        name="찾기",
        description="라이엇 ID로 디스코드 사용자를 찾습니다."
    )
    @app_commands.describe(
        riot_id="찾을 라이엇 ID (예: winter#겨울밤)"
    )
    async def find_user(self, interaction: discord.Interaction, riot_id: str):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용 가능한 기능입니다.", ephemeral=True)
            return

        guild_id = interaction.guild.id # 길드 ID를 변수에 저장

        if not is_server_configured(guild_id):
            await interaction.response.send_message("❌ 이 서버는 아직 설정되지 않았습니다. 관리자에게 문의해주세요.", ephemeral=True)
            return

        if not is_feature_enabled(guild_id, 'registration'):
            await interaction.response.send_message("❌ 이 서버에서는 계정 연동 기능이 비활성화되어 있습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # guild_id 변수 사용

        try:
            query = """
                SELECT user_id, created_at, updated_at 
                FROM registrations 
                WHERE guild_id = $1 AND LOWER(riot_id) = LOWER($2)
            """
            row = await self.bot.pool.fetchrow(query, guild_id, riot_id)

            if row:
                member = interaction.guild.get_member(row['user_id'])

                embed = discord.Embed(
                    title="🔍 사용자 찾기 결과",
                    color=discord.Color.green(),
                    timestamp=interaction.created_at
                )

                if member:
                    embed.add_field(name="디스코드 사용자", value=f"{member.mention}\n({member.display_name})", inline=False)
                    embed.add_field(name="사용자 ID", value=f"`{member.id}`", inline=True)
                    embed.set_thumbnail(url=member.display_avatar.url)
                else:
                    embed.add_field(name="디스코드 사용자", value=f"User ID: `{row['user_id']}`\n(서버를 떠났거나 찾을 수 없음)",
                                    inline=False)

                embed.add_field(name="라이엇 ID", value=f"`{riot_id}`", inline=True)

                if row['created_at']:
                    embed.add_field(name="등록일", value=f"<t:{int(row['created_at'].timestamp())}:F>", inline=True)

                embed.set_footer(text=f"서버: {interaction.guild.name}")

                await interaction.followup.send(embed=embed, ephemeral=True)
                # extra={'guild_id': guild_id} 추가
                self.logger.info(
                    f"{interaction.user} found user for Riot ID: {riot_id} -> {row['user_id']} (서버: {interaction.guild.name})", extra={'guild_id': guild_id})
            else:
                embed = discord.Embed(
                    title="❌ 사용자를 찾을 수 없습니다",
                    description=f"라이엇 ID `{riot_id}`로 등록된 사용자가 이 서버에 없습니다.",
                    color=discord.Color.red()
                )
                embed.set_footer(text=f"서버: {interaction.guild.name}")

                await interaction.followup.send(embed=embed, ephemeral=True)
                # extra={'guild_id': guild_id} 추가
                self.logger.info(
                    f"{interaction.user} tried to find user for Riot ID: {riot_id} but none was found. (서버: {interaction.guild.name})", extra={'guild_id': guild_id})

        except Exception as e:
            # extra={'guild_id': guild_id} 추가
            self.logger.error(
                f"❌ Database error during user search for {interaction.user} (서버: {interaction.guild.name}): {e}", extra={'guild_id': guild_id})
            await interaction.followup.send(
                f"❌ 데이터베이스 오류가 발생했습니다: `{e}`", ephemeral=True
            )

    @app_commands.command(
        name="연동해제",
        description="등록된 라이엇 ID 연동을 해제합니다."
    )
    async def unregister(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버에서만 사용 가능한 기능입니다.", ephemeral=True)
            return

        guild_id = interaction.guild.id # 길드 ID를 변수에 저장

        if not is_server_configured(guild_id):
            await interaction.response.send_message("❌ 이 서버는 아직 설정되지 않았습니다. 관리자에게 문의해주세요.", ephemeral=True)
            return

        if not is_feature_enabled(guild_id, 'registration'):
            await interaction.response.send_message("❌ 이 서버에서는 계정 연동 기능이 비활성화되어 있습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        discord_id = interaction.user.id
        # guild_id 변수 사용

        try:
            # First check if user has a registration
            check_query = "SELECT riot_id FROM registrations WHERE user_id = $1 AND guild_id = $2"
            existing = await self.bot.pool.fetchrow(check_query, discord_id, guild_id)

            if not existing:
                embed = discord.Embed(
                    title="❌ 연동된 계정이 없습니다",
                    description="연동해제할 라이엇 ID가 없습니다.",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Delete the registration
            delete_query = "DELETE FROM registrations WHERE user_id = $1 AND guild_id = $2"
            await self.bot.pool.execute(delete_query, discord_id, guild_id)

            embed = discord.Embed(
                title="✅ 연동 해제 완료",
                description=f"라이엇 ID `{existing['riot_id']}` 연동이 해제되었습니다.",
                color=discord.Color.green(),
                timestamp=interaction.created_at
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"서버: {interaction.guild.name}")

            await interaction.followup.send(embed=embed, ephemeral=True)

            # Log to server's log channel if configured
            log_channel_id = get_channel_id(guild_id, 'log_channel')
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                if log_channel:
                    try:
                        log_embed = discord.Embed(
                            title="🔓 계정 연동 해제",
                            description=f"{interaction.user.mention}님이 라이엇 ID 연동을 해제했습니다.",
                            color=discord.Color.orange(),
                            timestamp=interaction.created_at
                        )
                        log_embed.add_field(name="해제된 라이엇 ID", value=existing['riot_id'], inline=True)
                        log_embed.add_field(name="사용자", value=f"{interaction.user} ({interaction.user.id})",
                                            inline=True)
                        log_embed.set_thumbnail(url=interaction.user.display_avatar.url)
                        await log_channel.send(embed=log_embed)
                    except Exception as e:
                        # Log channel send failure does not need guild_id in extra if it's a general error
                        self.logger.error(f"Failed to send log message: {e}")

            # extra={'guild_id': guild_id} 추가
            self.logger.info(
                f"✅ {interaction.user} unregistered Riot ID: {existing['riot_id']} (서버: {interaction.guild.name})", extra={'guild_id': guild_id})

        except Exception as e:
            # extra={'guild_id': guild_id} 추가
            self.logger.error(
                f"❌ Database error during unregistration for {interaction.user} (서버: {interaction.guild.name}): {e}", extra={'guild_id': guild_id})
            await interaction.followup.send(
                f"❌ 데이터베이스 오류가 발생했습니다: `{e}`", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Registration(bot))