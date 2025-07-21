import discord
from discord.ext import commands
from discord import File
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os
import asyncio
import traceback

from utils import config
from utils.logger import get_logger

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
BG_PATH      = os.path.join(BASE_DIR, "..", "assets", "welcome_bg.png")
FONT_PATH_KR = os.path.join(BASE_DIR, "..", "assets", "fonts", "NotoSansKR-Bold.ttf")
FONT_SIZE    = 72

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
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        self.logger.info("WelcomeCog 초기화 완료.")

    async def make_welcome_card(self, member: discord.Member) -> BytesIO:
        try:
            bg = Image.open(BG_PATH).convert("RGBA")
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
                self.logger.error(f"❌ [welcome] 아바타 가져오기 실패: {e} for {member.display_name} ({member.id})\n{traceback.format_exc()}")
                avatar_bytes = None

            if avatar_bytes:
                avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")
                mask = Image.new('L', (128, 128), 0)
                draw_mask = ImageDraw.Draw(mask)
                draw_mask.ellipse((0, 0, 128, 128), fill=255)
                avatar_size = 128
                # 아바타를 가로 및 세로로 가운데 정렬
                avatar_x = (img_width - avatar_size) // 2
                avatar_y = (img_height // 2) - (avatar_size // 2) - 50  # 축하 카드와 동일하게 약간 위로 조정

                # 마스크 적용 부분은 그대로 유지
                bg.paste(avatar, (avatar_x, avatar_y), mask)
            font = FONT
            text = f"환영합니다, {member.display_name}님!"
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

            # Draw text with 'mm' (middle-middle) anchor for precise centering
            draw.text((text_x, text_y), text, font=font, fill="white", anchor="mm")

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

        ch = self.bot.get_channel(config.WELCOME_CHANNEL_ID)
        self.logger.info(f"⚙️ 신규 회원 감지: {member.display_name} (ID: {member.id}); 환영 채널 ID: {config.WELCOME_CHANNEL_ID}")

        if not ch:
            self.logger.error(f"❌ 환영 채널 {config.WELCOME_CHANNEL_ID}을(를) 찾을 수 없습니다. WELCOME_CHANNEL_ID 확인 필요.")
            return

        card_buf = None
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
            embed = discord.Embed(
                title=f"{member.display_name}님, 환영합니다!",
                description="Exceed 클랜에 오신 것을 환영합니다! 함께 멋진 활동을 시작해요.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="1️⃣ 서버 규칙을 꼭 확인해 주세요", value=f"<#{config.RULES_CHANNEL_ID}>", inline=False)
            embed.add_field(name="2️⃣ 클랜에 지원하여 전체 서버에 접근해 보세요!", value=f"<#{config.INTERVIEW_PUBLIC_CHANNEL_ID}>",
                            inline=False)
            if file:
                embed.set_image(url="attachment://welcome.png")
            embed.set_footer(text="Exceed • 환영 메시지", icon_url=self.bot.user.display_avatar.url)
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
            await ch.send(content=member.mention, embed=embed, file=file,
                          allowed_mentions=discord.AllowedMentions(users=True))
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

        ch = self.bot.get_channel(config.GOODBYE_CHANNEL_ID)
        self.logger.info(f"⚙️ 회원 퇴장 감지: {member.display_name} (ID: {member.id}); 작별 채널 ID: {config.GOODBYE_CHANNEL_ID}")

        if not ch:
            self.logger.error(f"❌ 작별 채널 {config.GOODBYE_CHANNEL_ID}을(를) 찾을 수 없습니다. GOODBYE_CHANNEL_ID 확인 필요.")
            return

        try:
            embed = discord.Embed(
                title="회원 퇴장",
                description=f"👋 **{member.display_name}**님이 클랜을 떠났습니다.",
                color=discord.Color.dark_grey(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Exceed • 작별 인사", icon_url=self.bot.user.display_avatar.url)

            self.logger.info(f"👋 {member.display_name}님이 서버를 떠났습니다. 작별 메시지 전송 중…")
            await ch.send(embed=embed)
            self.logger.info(f"✅ {member.display_name}님을 위한 작별 메시지 전송 완료.")
        except discord.Forbidden:
            self.logger.error(f"❌ 작별 메시지를 보낼 권한이 없습니다 (채널 {ch.id}). 봇 권한을 확인해주세요.")
        except discord.HTTPException as e:
            self.logger.error(f"❌ 작별 메시지 전송 중 Discord HTTP 오류 발생: {e}\n{traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"❌ {member.display_name}님을 위한 작별 메시지 전송 실패: {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))