# cogs/welcomegoodbye.py - Updated for multi-server support
import discord
from discord.ext import commands
from discord import File
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os
import asyncio
import traceback

from utils.config import (
    get_channel_id,
    get_role_id,
    is_feature_enabled,
    get_server_setting,
    is_server_configured
)
from utils.logger import get_logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BG_PATH = os.path.join(BASE_DIR, "..", "assets", "welcome_bg.png")
FONT_PATH_KR = os.path.join(BASE_DIR, "..", "assets", "fonts", "NotoSansKR-Bold.ttf")
FONT_SIZE = 72

# Load font once at startup
try:
    FONT = ImageFont.truetype(FONT_PATH_KR, FONT_SIZE)
except OSError:
    FONT = ImageFont.load_default()
    print("⚠️ Fallback font used for welcome card; Korean may not render properly.")


class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = get_logger(
            "환영/인사 카드",
            bot=self.bot,
            discord_log_channel_id=0  # Will be set per guild
        )

        self.logger.info("환영 및 작별 메시지 기능이 초기화되었습니다.")

    async def make_welcome_card(self, member: discord.Member) -> BytesIO:
        try:
            # Check if custom background exists for this server
            guild_bg_path = get_server_setting(member.guild.id, 'welcome_bg_path', BG_PATH)
            if not os.path.exists(guild_bg_path):
                guild_bg_path = BG_PATH

            bg = Image.open(guild_bg_path).convert("RGBA")
            draw = ImageDraw.Draw(bg)
            img_width, img_height = bg.size

            avatar_asset = member.display_avatar.with_size(128).with_format("png")
            try:
                avatar_bytes = await asyncio.wait_for(avatar_asset.read(), timeout=10)
            except asyncio.TimeoutError:
                self.logger.warning(f"⏳ [welcome] 아바타 가져오기 타임아웃: {member.display_name} ({member.id})")
                avatar_bytes = None
            except discord.HTTPException as e:
                self.logger.error(f"❌ [welcome] 아바타 HTTP 오류: {e} for {member.display_name} ({member.id})")
                avatar_bytes = None
            except Exception as e:
                self.logger.error(
                    f"❌ [welcome] 아바타 가져오기 실패: {e} for {member.display_name} ({member.id})\n{traceback.format_exc()}")
                avatar_bytes = None

            avatar_x = None
            avatar_y = None
            if avatar_bytes:
                avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")
                mask = Image.new('L', (128, 128), 0)
                draw_mask = ImageDraw.Draw(mask)
                draw_mask.ellipse((0, 0, 128, 128), fill=255)
                avatar_size = 128
                # 아바타를 가로 및 세로로 가운데 정렬
                avatar_x = (img_width - avatar_size) // 2
                avatar_y = (img_height // 2) - (avatar_size // 2) - 50  # 축하 카드와 동일하게 약간 위로 조정

                # 마스크 적용
                bg.paste(avatar, (avatar_x, avatar_y), mask)

            font = FONT
            # Get custom welcome message format from server settings
            welcome_message_format = get_server_setting(member.guild.id, 'welcome_message_format',
                                                        '환영합니다, {username}님!')
            text = welcome_message_format.format(username=member.display_name)

            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            # Calculate text_x for perfect horizontal centering of the text
            text_x = img_width // 2

            # Position text below the avatar, with a fixed padding, or in the middle if no avatar
            if avatar_y is not None:
                text_y = avatar_y + avatar_size + 20  # 20 pixels padding below avatar
            else:
                text_y = img_height // 2  # Center vertically if no avatar

            # Get text color from server settings
            text_color = get_server_setting(member.guild.id, 'welcome_text_color', 'white')

            # Draw text with 'mm' (middle-middle) anchor for precise centering
            draw.text((text_x, text_y), text, font=font, fill=text_color, anchor="mm")

            buf = BytesIO()
            bg.save(buf, "PNG")
            buf.seek(0)
            self.logger.debug(f"🎉 환영 카드 BytesIO 생성 완료: {member.display_name}")
            return buf
        except Exception as e:
            self.logger.error(f"❌ [welcome] 환영 카드 생성 중 치명적인 오류 발생: {e}\n{traceback.format_exc()}")
            raise

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            self.logger.info(f"🤖 봇 {member.display_name} ({member.id})이(가) 서버에 참여했습니다. 무시합니다.")
            return

        guild_id = member.guild.id

        # Check if server is configured and feature is enabled
        if not is_server_configured(guild_id):
            self.logger.info(f"길드 {guild_id}가 구성되지 않아 환영 메시지를 건너뜁니다.")
            return

        if not is_feature_enabled(guild_id, 'welcome_messages'):
            self.logger.info(f"길드 {guild_id}에서 환영 메시지가 비활성화되어 있습니다.")
            return

        welcome_channel_id = get_channel_id(guild_id, 'welcome_channel')
        if not welcome_channel_id:
            self.logger.warning(f"길드 {guild_id}에 환영 채널이 구성되지 않았습니다.")
            return

        ch = self.bot.get_channel(welcome_channel_id)
        self.logger.info(f"⚙️ 신규 회원 감지: {member.display_name} (ID: {member.id}); 환영 채널 ID: {welcome_channel_id}")

        if not ch:
            self.logger.error(f"❌ 환영 채널 {welcome_channel_id}을(를) 찾을 수 없습니다.")
            return

        # Check if welcome cards are enabled for this server
        enable_welcome_cards = get_server_setting(guild_id, 'enable_welcome_cards', True)

        card_buf = None
        if enable_welcome_cards:
            try:
                self.logger.info(f"🔧 [welcome] {member.display_name}님을 위한 환영 카드 생성 중…")
                card_buf = await self.make_welcome_card(member)
                self.logger.info(f"✅ [welcome] {member.display_name}님을 위한 환영 카드 생성 완료.")
            except Exception as e:
                self.logger.error(f"❌ [welcome] 환영 카드 생성 실패: {e}\n{traceback.format_exc()}")
                try:
                    await ch.send(f"⚠️ {member.mention}님, 환영합니다! 환영 카드 생성에 실패했습니다.")
                except discord.Forbidden:
                    self.logger.error(f"❌ 환영 메시지를 보낼 권한이 없습니다 (카드 생성 실패 후).")
                return

        file = None
        if card_buf:
            file = File(card_buf, filename="welcome.png")

        try:
            # Get custom embed settings from server config
            embed_title_format = get_server_setting(guild_id, 'welcome_embed_title', '{username}님, 환영합니다!')
            embed_description = get_server_setting(guild_id, 'welcome_embed_description',
                                                   '서버에 오신 것을 환영합니다! 함께 멋진 활동을 시작해요.')
            embed_color = get_server_setting(guild_id, 'welcome_embed_color', 'green')

            # Convert color string to discord.Color
            if embed_color == 'green':
                color = discord.Color.green()
            elif embed_color == 'blue':
                color = discord.Color.blue()
            elif embed_color == 'red':
                color = discord.Color.red()
            elif embed_color == 'gold':
                color = discord.Color.gold()
            elif embed_color == 'purple':
                color = discord.Color.purple()
            else:
                color = discord.Color.green()  # Default

            embed = discord.Embed(
                title=embed_title_format.format(username=member.display_name),
                description=embed_description,
                color=color,
                timestamp=datetime.now(timezone.utc)
            )

            # Add rules channel mention if configured
            rules_channel_id = get_channel_id(guild_id, 'rules_channel')
            if rules_channel_id:
                embed.add_field(name="・서버 규칙을 꼭 확인해 주세요", value=f"<#{rules_channel_id}>", inline=False)

            if file:
                embed.set_image(url="attachment://welcome.png")
            embed.set_footer(text="아날로그 • 환영 메시지", icon_url=self.bot.user.display_avatar.url)
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
            self.logger.debug(f"📝 [welcome] {member.display_name}님을 위한 임베드 빌드 완료.")
        except Exception as e:
            self.logger.error(f"❌ [welcome] 임베드 빌드 실패: {e}\n{traceback.format_exc()}")
            if file:
                file.close()
            try:
                await ch.send(f"⚠️ {member.mention}님, 환영합니다! 임베드 메시지 생성에 실패했습니다.")
            except discord.Forbidden:
                self.logger.error(f"❌ 환영 메시지를 보낼 권한이 없습니다 (임베드 빌드 실패 후).")
            return

        try:
            self.logger.info(f"🔧 [welcome] {member.display_name}님을 위한 환영 메시지 전송 중…")

            # Check if we should mention the user
            mention_user = get_server_setting(guild_id, 'mention_on_welcome', True)
            content = member.mention if mention_user else None

            await ch.send(content=content, embed=embed, file=file,
                          allowed_mentions=discord.AllowedMentions(users=mention_user))
            self.logger.info(f"✅ [welcome] {member.display_name}님을 위한 환영 메시지 전송 완료.")
        except discord.Forbidden:
            self.logger.error(f"❌ [welcome] 환영 메시지를 보낼 권한이 없습니다 (채널 {ch.id}). 봇 권한을 확인해주세요.")
        except discord.HTTPException as e:
            self.logger.error(f"❌ [welcome] 환영 메시지 전송 중 Discord HTTP 오류 발생: {e}\n{traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"❌ [welcome] 환영 메시지 전송 실패: {e}\n{traceback.format_exc()}")
        finally:
            if file:
                file.close()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            self.logger.info(f"🤖 봇 {member.display_name} ({member.id})이(가) 서버에서 나갔습니다. 무시합니다.")
            return

        guild_id = member.guild.id

        # Check if server is configured and feature is enabled
        if not is_server_configured(guild_id):
            self.logger.info(f"길드 {guild_id}가 구성되지 않아 작별 메시지를 건너뜁니다.")
            return

        if not is_feature_enabled(guild_id, 'welcome_messages'):
            self.logger.info(f"길드 {guild_id}에서 환영/작별 메시지가 비활성화되어 있습니다.")
            return

        goodbye_channel_id = get_channel_id(guild_id, 'goodbye_channel')
        if not goodbye_channel_id:
            self.logger.warning(f"길드 {guild_id}에 작별 채널이 구성되지 않았습니다.")
            return

        ch = self.bot.get_channel(goodbye_channel_id)
        self.logger.info(f"⚙️ 회원 퇴장 감지: {member.display_name} (ID: {member.id}); 작별 채널 ID: {goodbye_channel_id}")

        if not ch:
            self.logger.error(f"❌ 작별 채널 {goodbye_channel_id}을(를) 찾을 수 없습니다.")
            return

        try:
            # Get custom goodbye settings from server config
            goodbye_title = get_server_setting(guild_id, 'goodbye_title', '회원 퇴장')
            goodbye_description_format = get_server_setting(guild_id, 'goodbye_description',
                                                            '👋 **{username}**님이 클랜을 떠났습니다.')
            goodbye_color = get_server_setting(guild_id, 'goodbye_color', 'dark_grey')

            # Convert color string to discord.Color
            if goodbye_color == 'dark_grey':
                color = discord.Color.dark_grey()
            elif goodbye_color == 'red':
                color = discord.Color.red()
            elif goodbye_color == 'orange':
                color = discord.Color.orange()
            elif goodbye_color == 'blue':
                color = discord.Color.blue()
            else:
                color = discord.Color.dark_grey()  # Default

            embed = discord.Embed(
                title=goodbye_title,
                description=goodbye_description_format.format(username=member.display_name),
                color=color,
                timestamp=datetime.now(timezone.utc)
            )

            # Check if we should show avatar in goodbye messages
            show_avatar = get_server_setting(guild_id, 'show_avatar_on_goodbye', True)
            if show_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)

            embed.set_footer(text="아날로그 • 작별 인사", icon_url=self.bot.user.display_avatar.url)

            self.logger.info(f"👋 {member.display_name}님이 서버를 떠났습니다. 작별 메시지 전송 중…")
            await ch.send(embed=embed)
            self.logger.info(f"✅ {member.display_name}님을 위한 작별 메시지 전송 완료.")
        except discord.Forbidden:
            self.logger.error(f"❌ 작별 메시지를 보낼 권한이 없습니다 (채널 {ch.id}). 봇 권한을 확인해주세요.")
        except discord.HTTPException as e:
            self.logger.error(f"❌ 작별 메시지 전송 중 Discord HTTP 오류 발생: {e}\n{traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"❌ {member.display_name}님을 위한 작별 메시지 전송 실패: {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Handle bot joining a new guild"""
        self.logger.info(f"Bot joined new guild for welcome/goodbye: {guild.name} ({guild.id})")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Handle bot leaving a guild"""
        self.logger.info(f"Bot left guild for welcome/goodbye: {guild.name} ({guild.id})")


async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))