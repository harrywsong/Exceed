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

# Load font once at startup (fallback if missing)
try:
    FONT = ImageFont.truetype(FONT_PATH_KR, FONT_SIZE)
except OSError:
    FONT = ImageFont.load_default()
    print("⚠️ fallback font used; Korean may not render properly")

class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def make_welcome_card(self, member: discord.Member) -> BytesIO:
        bg = Image.open(BG_PATH).convert("RGBA")
        draw = ImageDraw.Draw(bg)

        # Fetch avatar bytes
        avatar_asset = member.display_avatar.with_size(128).with_format("png")
        try:
            avatar_bytes = await asyncio.wait_for(avatar_asset.read(), timeout=5)
        except Exception as e:
            await get_logger(self.bot, f"❌ [welcome] 아바타 가져오기 실패: {e}")
            avatar_bytes = None

        if avatar_bytes:
            avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")
            bg.paste(avatar, (40, bg.height // 2 - 64), avatar)

        # Draw welcome text
        font = FONT
        text = f"환영합니다, {member.display_name}님!"
        bbox = draw.textbbox((0, 0), text, font=font)
        x = 200
        y = (bg.height // 2) - ((bbox[3] - bbox[1]) // 2)
        draw.text((x, y), text, font=font, fill="white")

        buf = BytesIO()
        bg.save(buf, "PNG")
        buf.seek(0)
        return buf

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        ch = self.bot.get_channel(config.WELCOME_CHANNEL_ID)
        logger = get_logger("welcome", bot=self.bot, discord_log_channel_id=config.LOG_CHANNEL_ID)
        logger.info(f"⚙️ 신규 회원 감지: {member} (ID: {member.id}); 채널 → {config.WELCOME_CHANNEL_ID}")

        if not ch:
            logger.error("❌ 환영 채널을 찾을 수 없습니다. WELCOME_CHANNEL_ID 확인 필요")
            return

        try:
            logger.info("🔧 [welcome] 환영 카드 생성 중…")
            card_buf = await self.make_welcome_card(member)
            logger.info("✅ [welcome] 환영 카드 생성 완료")
        except Exception:
            traceback.print_exc()
            logger.error("❌ [welcome] 환영 카드 생성 실패")
            return await ch.send(f"⚠️ 환영 카드 생성 실패")

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
            embed.set_image(url="attachment://welcome.png")
            embed.set_footer(text="Exceed • 환영 메시지", icon_url=self.bot.user.display_avatar.url)
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        except Exception:
            traceback.print_exc()
            logger.error("❌ [welcome] 임베드 빌드 실패")
            return

        try:
            logger.info("🔧 [welcome] 환영 메시지 전송 중…")
            await ch.send(content=member.mention, embed=embed, file=file,
                          allowed_mentions=discord.AllowedMentions(users=True))
            logger.info("✅ [welcome] 환영 메시지 전송 완료")
        except Exception as e:
            traceback.print_exc()
            logger.error(f"❌ [welcome] 환영 메시지 전송 실패: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        ch = self.bot.get_channel(config.GOODBYE_CHANNEL_ID)
        logger = get_logger("welcome", bot=self.bot, discord_log_channel_id=config.LOG_CHANNEL_ID)

        if not ch:
            logger.error("❌ 작별 채널을 찾을 수 없습니다. LEAVE_CHANNEL_ID 확인 필요")
            return

        embed = discord.Embed(
            title="회원 퇴장",
            description=f"**{member}**님이 클랜을 떠났습니다. 다음에 또 만나요! 👋",
            color=discord.Color.dark_grey(),
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Exceed • 작별 인사", icon_url=self.bot.user.display_avatar.url)

        logger.info(f"👋 {member.display_name}님이 서버를 떠났습니다.")
        await ch.send(embed=embed)


async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
