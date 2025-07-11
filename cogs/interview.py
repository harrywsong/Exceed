import asyncio
import os
from io import BytesIO

import re

import discord
from PIL import Image, ImageDraw
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from discord import TextStyle
import logging
import traceback
from discord import File
from datetime import datetime, timezone

from cogs.welcomegoodbye import BG_PATH, FONT
from utils import config
from utils.config import INTERVIEW_PUBLIC_CHANNEL_ID, INTERVIEW_PRIVATE_CHANNEL_ID, WELCOME_CHANNEL_ID, \
    RULES_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID, ACCEPTED_ROLE_ID, MEMBER_CHAT_CHANNEL_ID
from utils.logger import get_logger

logger = logging.getLogger("bot")

CONGRATS_BG_PATH = os.path.join("assets", "congrats_bg.gif")

APPLICANT_ROLE_ID = 1390188260956835893
GUEST_ROLE_ID = 1389711048461910057

class DecisionButtonView(discord.ui.View):
    def __init__(self, applicant_id: int = None, cog=None):
        super().__init__(timeout=None)  # Persistent view must have timeout=None
        self.applicant_id = applicant_id
        self.cog = cog

    def _extract_user_id(self, interaction: discord.Interaction) -> int | None:
        user_id = None
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            mention_match = re.search(r'<@!?(\d+)>', embed.description or "")
            if not mention_match:
                for field in embed.fields:
                    mention_match = re.search(r'<@!?(\d+)>', field.value)
                    if mention_match:
                        break
            if mention_match:
                user_id = int(mention_match.group(1))
        return user_id

    @discord.ui.button(label="합격", style=discord.ButtonStyle.success, custom_id="interview_pass")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)  # Defer immediately

        user_id = None
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            mention_match = re.search(r'<@!?(\d+)>', embed.description or "")
            if not mention_match:
                for field in embed.fields:
                    mention_match = re.search(r'<@!?(\d+)>', field.value)
                    if mention_match:
                        break
            if mention_match:
                user_id = int(mention_match.group(1))

        if not user_id:
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )
        member = interaction.guild.get_member(user_id)
        if not member:
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )

        try:
            role = interaction.guild.get_role(ACCEPTED_ROLE_ID)
            if not role:
                return await interaction.followup.send(
                    "❌ 합격 역할을 찾을 수 없습니다. 관리자에게 문의해주세요.",
                    ephemeral=True
                )

            await member.add_roles(role, reason="합격 처리됨")

            # Remove applicant role
            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="합격 처리로 인한 지원자 역할 제거")

            # Remove guest role
            guest_role = interaction.guild.get_role(GUEST_ROLE_ID)
            if guest_role and guest_role in member.roles:
                await member.remove_roles(guest_role, reason="합격 처리로 인한 게스트 역할 제거")

            await interaction.followup.send(
                f"✅ {member.mention}님을 합격 처리했습니다!"
            )
            if self.cog:
                await self.cog.send_welcome_message(member)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 역할을 부여할 권한이 없습니다.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ 오류 발생: {str(e)}",
                ephemeral=True
            )

    @discord.ui.button(label="테스트", style=discord.ButtonStyle.secondary, custom_id="interview_test")
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        # Permission check
        if not interaction.user.guild_permissions.administrator:
            return await interaction.followup.send("❌ 이 작업을 수행할 권한이 없습니다. 관리자만 사용할 수 있습니다.", ephemeral=True)

        user_id = self._extract_user_id(interaction)
        if not user_id:
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if not member:
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        test_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
        if not test_role:
            return await interaction.followup.send("❌ 테스트 역할을 찾을 수 없습니다.", ephemeral=True)

        try:
            await member.add_roles(test_role, reason="테스트 역할 부여 (관리자 승인)")

            await member.send(
                "안녕하세요.\n\n"
                "Exceed 클랜에 지원해 주셔서 진심으로 감사드립니다.\n"
                "지원자님의 가능성과 열정을 더욱 알아보기 위해 **테스트 역할**을 부여드렸습니다.\n\n"
                "해당 역할을 통해 테스트 기간 동안 서버에서 자유롭게 활동해 주시고,\n"
                "운영진은 지원자님의 활동 및 소통을 바탕으로 최종 결정을 내리게 됩니다.\n\n"
                "Exceed는 팀워크와 커뮤니티 분위기를 중시하는 만큼,\n"
                "테스트 기간 중 적극적인 참여와 긍정적인 소통을 기대하겠습니다.\n\n"
                "궁금하신 사항이나 불편한 점이 있으시면 언제든지 운영진에게 문의해 주세요.\n"
                "문의는 아래 채널을 통해 주셔도 됩니다:\n\n"
                "https://discord.com/channels/1389527318699053178/1389742771253805077\n\n"
                "다시 한번 지원해 주셔서 감사드리며, 앞으로의 활동을 기대하겠습니다!\n\n"
                "감사합니다.\n\n"
                "📌 *이 메시지는 자동 발송되었으며, 이 봇에게 직접 답장하셔도 운영진은 내용을 확인할 수 없습니다.*"
            )

            await interaction.followup.send(f"🟡 {member.mention}님에게 테스트 역할을 부여했습니다.")

            # Use logger properly (assuming self.cog.logger or some logger instance)
            if self.cog and hasattr(self.cog, "logger"):
                self.cog.logger.info(f"{member} ({member.id})님에게 테스트 역할을 부여했습니다.")

        except discord.Forbidden:
            await interaction.followup.send("❌ 역할 부여 또는 DM 전송 권한이 없습니다.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 오류 발생: {str(e)}", ephemeral=True)

    @discord.ui.button(label="불합격", style=discord.ButtonStyle.danger, custom_id="interview_fail")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)  # Defer immediately

        if not interaction.message.embeds:
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )
        embed = interaction.message.embeds[0]

        mention_match = re.search(r'<@!?(\d+)>', embed.description or "")
        if not mention_match:
            for field in embed.fields:
                mention_match = re.search(r'<@!?(\d+)>', field.value)
                if mention_match:
                    break
        if not mention_match:
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )

        user_id = int(mention_match.group(1))
        member = interaction.guild.get_member(user_id)
        if not member:
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )
        try:
            await member.send(
                "안녕하세요. \n\n"
                "먼저 Exceed 클랜에 관심을 가져주시고 지원해 주셔서 진심으로 감사드립니다.\n"
                "안타깝게도 이번에는 여러 사유로 인해 함께하지 못하게 되었습니다.\n"
                "지원자님의 열정과 노력은 충분히 높이 평가하지만, 현재 클랜의 상황과 다양한 요소들을 종합적으로 고려한 결과임을 너그러이 이해해 주시길 바랍니다.\n"
                "앞으로도 지속적인 발전이 있으시길 진심으로 응원하며, 상황이 괜찮아지면 언제든지 다시 지원해 주시길 바랍니다. \n\n"
                "Exceed는 언제나 열려 있으며, 다음 기회에 꼭 함께할 수 있기를 기대하겠습니다.\n\n"
                "궁금한 점이 있으시면 언제든지 운영진에게 문의하시거나, 아래 채널을 통해 연락 주시기 바랍니다:  \n\n"
                "https://discord.com/channels/1389527318699053178/1389742771253805077\n\n"
                "감사합니다.\n\n"
                "📌 *이 메시지는 자동 발송되었으며, 이 봇에게 직접 답장하셔도 운영진은 내용을 확인할 수 없습니다.*"
            )

            # Remove applicant role
            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="불합격 처리로 인한 지원자 역할 제거")

            await interaction.followup.send(f"❌ {member.mention}님을 불합격 처리했습니다.")
        except discord.Forbidden:
            await interaction.followup.send("DM을 보낼 수 없습니다.", ephemeral=True)

class InterviewModal(Modal, title="인터뷰 사전 질문"):
    def __init__(self):
        super().__init__()
        self.answers = {}

        self.add_item(TextInput(
            label="활동 지역 (서부/중부/동부)",
            placeholder="예: 중부",
            style=TextStyle.short,
            required=True,
            max_length=20
        ))
        self.add_item(TextInput(
            label="인게임 이름 및 태그 (예: 이름#태그)",
            placeholder="예: 라이엇이름#라이엇태그",
            style=TextStyle.short,
            required=True,
            max_length=50
        ))
        self.add_item(TextInput(
            label="가장 자신있는 역할",
            placeholder="예: 타격대, 감시자, 척후대 등",
            style=TextStyle.short,
            required=True,
            max_length=30
        ))
        self.add_item(TextInput(
            label="프리미어 팀 참가 의향",
            placeholder="예: 네 / 아니요",
            style=TextStyle.short,
            required=True,
            max_length=10
        ))
        self.add_item(TextInput(
            label="지원 동기",
            placeholder="Exceed에 지원하게 된 이유를 간단히 적어주세요.",
            style=TextStyle.paragraph,
            required=True,
            max_length=300
        ))

    async def on_submit(self, interaction: discord.Interaction):
        for item in self.children:
            self.answers[item.label] = item.value.strip()

        region = self.answers.get("활동 지역 (서부/중부/동부)", "")
        if region not in ("서부", "중부", "동부"):
            return await interaction.response.send_message(
                "❌ 올바른 활동 지역을 입력해주세요 (서부, 중부, 동부 중 하나).",
                ephemeral=True
            )

        cog = interaction.client.get_cog("InterviewRequestCog")  # Get the cog instance
        if not cog:
            return await interaction.response.send_message(
                "❌ 인터뷰 코그를 찾을 수 없습니다.",
                ephemeral=True
            )

        private_channel = interaction.guild.get_channel(cog.private_channel_id)
        if not private_channel:
            return await interaction.response.send_message(
                "❌ 비공개 채널을 찾을 수 없습니다.",
                ephemeral=True
            )

        embed = discord.Embed(
            title="📝 인터뷰 요청 접수",
            description=f"{interaction.user.mention} 님이 인터뷰를 요청했습니다.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_author(name="Exceed 인터뷰 시스템")

        for question, answer in self.answers.items():
            embed.add_field(
                name=f"❓ {question}",
                value=f"> {answer or '*응답 없음*'}",
                inline=False
            )

        view = DecisionButtonView(applicant_id=interaction.user.id, cog=cog)
        await private_channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            "✅ 인터뷰 요청이 성공적으로 전송되었습니다!",
            ephemeral=True
        )

class InterviewView(View):
    def __init__(self, private_channel_id: int, cog):
        super().__init__(timeout=None)
        self.private_channel_id = private_channel_id
        self.cog = cog

    @discord.ui.button(label="인터뷰 요청 시작하기", style=discord.ButtonStyle.primary, custom_id="start_interview")
    async def start_interview(self, interaction: discord.Interaction, button: Button):
        modal = InterviewModal()
        await interaction.response.send_modal(modal)

class InterviewRequestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.public_channel_id = INTERVIEW_PUBLIC_CHANNEL_ID
        self.private_channel_id = INTERVIEW_PRIVATE_CHANNEL_ID

    async def make_congrats_card(self, member: discord.Member) -> BytesIO:
        bg = Image.open(CONGRATS_BG_PATH).convert("RGBA")
        draw = ImageDraw.Draw(bg)

        # Fetch avatar bytes
        avatar_asset = member.display_avatar.with_size(128).with_format("png")
        try:
            avatar_bytes = await asyncio.wait_for(avatar_asset.read(), timeout=5)
        except Exception as e:
            await get_logger(self.bot, f"❌ [congrats] 아바타 가져오기 실패: {e}")
            avatar_bytes = None

        if avatar_bytes:
            avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")
            bg.paste(avatar, (40, bg.height // 2 - 64), avatar)

        # Draw congratulation text
        font = FONT
        text = f"축하합니다, {member.display_name}님!"
        bbox = draw.textbbox((0, 0), text, font=font)
        x = 200
        y = (bg.height // 2) - ((bbox[3] - bbox[1]) // 2)
        draw.text((x, y), text, font=font, fill="white")

        buf = BytesIO()
        bg.save(buf, "PNG")
        buf.seek(0)
        return buf

    async def send_welcome_message(self, member: discord.Member):
        """Send welcome message to welcome channel"""
        channel = self.bot.get_channel(WELCOME_CHANNEL_ID)
        if not channel:
            return logger.error("환영 채널을 찾을 수 없습니다")

        try:
            card_buf = await self.make_congrats_card(member)
            if not card_buf:
                raise ValueError("Welcome card generation failed")

            file = File(card_buf, filename="welcome.png")

            embed = discord.Embed(
                title=f"🎉 {member.display_name}님, Exceed 클랜에 합격하셨습니다!",
                description="축하드립니다! 공식 클랜 멤버가 되신 것을 진심으로 환영합니다.",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="1️⃣ 클랜 규칙을 꼭 확인해 주세요!", value=f"<#{config.RULES_CHANNEL_ID}>", inline=False)
            embed.add_field(name="2️⃣ 역할지급 채널에서 원하는 역할을 선택해 주세요.", value=f"<#{config.ROLE_ASSIGN_CHANNEL_ID}>", inline=False)
            embed.add_field(name="3️⃣ 멤버 전용 채팅방을 확인해 보세요.", value=f"<#{config.MEMBER_CHAT_CHANNEL_ID}>", inline=False)
            embed.add_field(name="4️⃣ 클랜 MMR 시스템을 기반으로 한 클랜 리더보드를 확인해 보세요.", value=f"<#{config.CLAN_LEADERBOARD_CHANNEL_ID}>", inline=False)
            embed.set_image(url="attachment://welcome.png")
            embed.set_footer(text="Exceed • 합격 축하 메시지", icon_url=self.bot.user.display_avatar.url)

            await channel.send(
                content=member.mention,
                embed=embed,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True))

        except Exception as e:
            logger.error(f"환영 메시지 전송 실패: {str(e)}")
            traceback.print_exc()

    async def send_interview_request_message(self):
        channel = self.bot.get_channel(self.public_channel_id)
        if not channel:
            return logger.error(f"공개 채널 ID {self.public_channel_id}를 찾을 수 없습니다.")

        try:
            await channel.purge(limit=None)

            # 1. 가입 조건 안내 임베드 (원본 이모지 사용)
            rules_embed = discord.Embed(
                title="🎯 XCD 발로란트 클랜 가입 조건 안내",
                description="📜 최종 업데이트: 2025.07.06",
                color=discord.Color.orange()
            )
            rules_embed.add_field(
                name="가입 전 아래 조건을 반드시 확인해 주세요.",
                value=(
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "🔞 1. 나이 조건\n"
                    "・만 20세 이상 (2005년생 이전)\n"
                    "・성숙한 커뮤니케이션과 책임감 있는 행동을 기대합니다.\n\n"
                    "🎮 2. 실력 조건\n"
                    "・현재 티어 골드 이상 (에피소드 기준)\n"
                    "・트라이아웃(스크림 테스트)으로 실력 확인 가능\n"
                    "・게임 이해도 & 팀워크도 함께 평가\n\n"
                    "💬 3. 매너 & 소통\n"
                    "・욕설/무시/조롱/반말 등 비매너 언행 금지\n"
                    "・피드백을 받아들이고 긍정적인 태도로 게임 가능\n"
                    "・디스코드 마이크 필수\n\n"
                    "⏱️ 4. 활동성\n"
                    "・주 3회 이상 접속 & 게임 참여 가능자\n"
                    "・대회/스크림/내전 등 일정에 적극 참여할 의향 있는 분\n"
                    "・30일 이상 미접속 시 자동 탈퇴 처리 가능\n\n"
                    "🚫 5. 제한 대상\n"
                    "・다른 클랜과 겹치는 활동 중인 유저\n"
                    "・트롤, 욕설, 밴 이력 등 제재 기록 있는 유저\n"
                    "・대리/부계정/계정 공유 등 비정상 활동\n"
                    "━━━━━━━━━━━━━━━━━━━━━"
                ),
                inline=False
            )
            rules_embed.add_field(
                name="📋 가입 절차",
                value=(
                    "1️⃣ 디스코드 서버 입장\n"
                    "2️⃣ 가입 지원서 작성 or 인터뷰\n"
                    "3️⃣ 트라이아웃 or 최근 경기 클립 확인\n"
                    "4️⃣ 운영진 승인 → 역할 부여 후 가입 완료"
                ),
                inline=False
            )
            rules_embed.add_field(
                name="🧠 FAQ",
                value=(
                    "Q. 마이크 없으면 가입 안 되나요?\n"
                    "→ 네. 음성 소통은 필수입니다. 텍스트만으로는 활동이 어렵습니다.\n\n"
                    "Q. 골드 미만인데 들어갈 수 있나요?\n"
                    "→ 트라이아웃으로 팀워크/이해도 확인 후 예외 승인될 수 있습니다."
                ),
                inline=False
            )
            rules_embed.set_footer(
                text="✅ 가입 후 일정 기간 적응 평가 기간이 있으며\n"
                     "매너, 참여도 부족 시 경고 없이 탈퇴될 수 있습니다.\n\n"
                     "📌 본 안내는 클랜 운영 상황에 따라 변경될 수 있습니다."
            )

            await channel.send(embed=rules_embed)

            # 2. 인터뷰 안내 임베드 + 버튼 (기존 그대로)
            interview_embed = discord.Embed(
                title="✨ 인터뷰 요청 안내 ✨",
                description=(
                    "Exceed 클랜에 지원하고 싶으신가요?\n"
                    "아래 버튼을 눌러 인터뷰 요청을 시작하세요.\n"
                    "신속하게 확인 후 연락드리겠습니다."
                ),
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            interview_embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/1041/1041916.png")
            interview_embed.set_footer(text="Exceed • 인터뷰 시스템")
            interview_embed.set_author(
                name="Exceed 인터뷰 안내",
                icon_url="https://cdn-icons-png.flaticon.com/512/295/295128.png"
            )

            await channel.send(embed=interview_embed, view=InterviewView(self.private_channel_id, self))
            logger.info("📨・지원서-제출 채널에 가입 조건 안내 및 인터뷰 버튼을 게시했습니다.")

        except Exception as e:
            logger.error(f"인터뷰 요청 메시지 전송 실패: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.send_interview_request_message()

    @discord.app_commands.command(
        name="request_interview",
        description="인터뷰 요청 메시지를 다시 보냅니다 (관리자용)"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def slash_request_interview(self, interaction: discord.Interaction):
        await self.send_interview_request_message()
        await interaction.response.send_message(
            "인터뷰 요청 메시지를 갱신했습니다!",
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(InterviewRequestCog(bot))