import asyncio
import os
from io import BytesIO

import re

import discord
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from discord import TextStyle
import traceback
from discord import File
from datetime import datetime, timezone

from typing import Optional

from utils import config
# NEW: Import gspread_utils
from utils.gspread_utils import GSpreadClient
from utils.config import INTERVIEW_PUBLIC_CHANNEL_ID, INTERVIEW_PRIVATE_CHANNEL_ID, WELCOME_CHANNEL_ID, \
    RULES_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID, ACCEPTED_ROLE_ID, MEMBER_CHAT_CHANNEL_ID
from utils.logger import get_logger

from utils.config import APPLICANT_ROLE_ID, GUEST_ROLE_ID


class DecisionButtonView(discord.ui.View):
    def __init__(self, applicant_id: int = None, cog=None):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.cog = cog

    def _extract_user_id(self, interaction: discord.Interaction) -> Optional[int]:
        user_id = None
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            # Check description first for mention
            mention_match = re.search(r'<@!?(\d+)>', embed.description or "")
            if not mention_match:
                # If not in description, check fields
                for field in embed.fields:
                    mention_match = re.search(r'<@!?(\d+)>', field.value)
                    if mention_match:
                        break  # Found in a field, break loop
            if mention_match:
                user_id = int(mention_match.group(1))
        return user_id

    # NEW: Helper function to extract answers from the embed
    def _extract_answers_from_embed(self, interaction: discord.Interaction) -> dict:
        answers = {}
        if not interaction.message.embeds:
            return answers
        embed = interaction.message.embeds[0]
        for field in embed.fields:
            # Cleans up the question key from "❓ " prefix and strips whitespace
            # For your specific modal's questions, match them exactly
            original_question_label = field.name.replace("❓ ", "").strip()

            # Map the displayed question to the actual key used in the modal/gspread
            if original_question_label == "활동 지역 (서부/중부/동부)":
                question_key = "활동 지역 (서부/중부/동부)"
            elif original_question_label == "인게임 이름 및 태그 (예: 이름#태그)":
                question_key = "인게임 이름 및 태그 (예: 이름#태그)"
            elif original_question_label == "가장 자신있는 역할":
                question_key = "가장 자신있는 역할"
            elif original_question_label == "프리미어 팀 참가 의향":
                question_key = "프리미어 팀 참가 의향"
            elif original_question_label == "지원 동기":
                question_key = "지원 동기"
            else:
                question_key = original_question_label  # Fallback if not a known question

            # Cleans up the answer value from "> " prefix and "*응답 없음*" and strips whitespace
            answer = field.value.replace("> ", "").replace("*응답 없음*", "").strip()
            answers[question_key] = answer
        return answers

    @discord.ui.button(label="합격", style=discord.ButtonStyle.success, custom_id="interview_pass")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        # Check if the interaction user has administrator permissions
        if not interaction.user.guild_permissions.administrator:
            self.cog.logger.warning(f"관리자 권한 없는 사용자 ({interaction.user.display_name})가 합격 버튼을 누름.")
            return await interaction.followup.send("❌ 이 버튼을 사용할 권한이 없습니다.", ephemeral=True)

        user_id = self._extract_user_id(interaction)
        if not user_id:
            self.cog.logger.warning(f"합격 처리 시 user_id를 찾을 수 없습니다. 메시지 ID: {interaction.message.id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"합격 처리 시 멤버를 찾을 수 없습니다. User ID: {user_id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        try:
            role = interaction.guild.get_role(ACCEPTED_ROLE_ID)
            if not role:
                self.cog.logger.error(f"❌ 합격 역할 ID {ACCEPTED_ROLE_ID}을(를) 찾을 수 없습니다. 관리자에게 문의해주세요.")
                return await interaction.followup.send(
                    "❌ 합격 역할을 찾을 수 없습니다. 관리자에게 문의해주세요.",
                    ephemeral=True
                )

            if role in member.roles:
                return await interaction.followup.send(f"✅ {member.mention}님은 이미 '{role.name}' 역할을 가지고 있습니다.",
                                                       ephemeral=True)

            await member.add_roles(role, reason="합격 처리됨")
            self.cog.logger.info(f"✅ {member.display_name} ({member.id})님을 합격 처리했습니다. 역할 '{role.name}' 부여.")

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="합격 처리로 인한 지원자 역할 제거")
                self.cog.logger.info(f"지원자 역할 '{applicant_role.name}'을(를) {member.display_name}님에게서 제거했습니다.")

            guest_role = interaction.guild.get_role(GUEST_ROLE_ID)
            if guest_role and guest_role in member.roles:
                await member.remove_roles(guest_role, reason="합격 처리로 인한 게스트 역할 제거")
                self.cog.logger.info(f"게스트 역할 '{guest_role.name}'을(를) {member.display_name}님에게서 제거했습니다.")

            # --- NEW: Google Sheets Integration ---
            answers = self._extract_answers_from_embed(interaction)
            if self.cog.gspread_client:
                # Remove from test sheet (if they were there)
                self.cog.gspread_client.remove_from_test_sheet(member.id,
                                                               member.display_name)  # No await needed here, it's synchronous now
                # Add to main members sheet
                self.cog.gspread_client.add_to_members_sheet(member.id, member.display_name,
                                                             answers)  # No await needed here, it's synchronous now
            # --- End of Google Sheets Integration ---

            await interaction.followup.send(
                f"✅ {member.mention}님을 합격 처리했습니다!"
            )
            if self.cog:
                await self.cog.send_welcome_message(member)

        except discord.Forbidden:
            self.cog.logger.error(f"❌ 역할을 부여할 권한이 없습니다. 봇 권한을 확인해주세요. {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 역할을 부여할 권한이 없습니다. 봇 권한을 확인해주세요.",
                ephemeral=True
            )
        except Exception as e:
            self.cog.logger.error(f"❌ 합격 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(
                f"❌ 오류 발생: {str(e)}",
                ephemeral=True
            )

    @discord.ui.button(label="테스트", style=discord.ButtonStyle.secondary, custom_id="interview_test")
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            self.cog.logger.warning(
                f"{interaction.user.display_name} ({interaction.user.id})님이 테스트 버튼을 사용하려 했으나 권한이 없습니다.")
            return await interaction.followup.send("❌ 이 작업을 수행할 권한이 없습니다. 관리자만 사용할 수 있습니다.", ephemeral=True)

        user_id = self._extract_user_id(interaction)
        if not user_id:
            self.cog.logger.warning(f"테스트 처리 시 user_id를 찾을 수 없습니다. 메시지 ID: {interaction.message.id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"테스트 처리 시 멤버를 찾을 수 없습니다. User ID: {user_id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        test_role = interaction.guild.get_role(
            APPLICANT_ROLE_ID)  # Assuming APPLICANT_ROLE_ID is for 'Test' or 'Applicant'
        if not test_role:
            self.cog.logger.error(f"❌ 테스트 역할 ID {APPLICANT_ROLE_ID}을(를) 찾을 수 없습니다. 설정 확인 필요.")
            return await interaction.followup.send("❌ 테스트 역할을 찾을 수 없습니다.", ephemeral=True)

        try:
            if test_role in member.roles:
                return await interaction.followup.send(f"🟡 {member.mention}님은 이미 테스트 역할을 가지고 있습니다.", ephemeral=True)

            await member.add_roles(test_role, reason="테스트 역할 부여 (관리자 승인)")
            self.cog.logger.info(f"🟡 {member.display_name} ({member.id})님에게 테스트 역할 '{test_role.name}'을(를) 부여했습니다.")

            # --- NEW: Google Sheets Integration ---
            answers = self._extract_answers_from_embed(interaction)
            if self.cog.gspread_client:
                self.cog.gspread_client.add_to_test_sheet(member.id, member.display_name,
                                                          answers)  # No await needed here
            # --- End of Google Sheets Integration ---

            try:
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
                self.cog.logger.info(f"🟡 {member.display_name}님에게 테스트 안내 DM 전송 완료.")
            except discord.Forbidden:
                self.cog.logger.warning(f"🟡 {member.display_name} ({member.id})님에게 DM을 보낼 수 없습니다. (DM이 비활성화되었거나 차단됨)")
                await interaction.followup.send(
                    f"🟡 {member.mention}님에게 테스트 역할을 부여했습니다. (DM 전송 실패: DM이 비활성화되었을 수 있습니다.)")
                return

            await interaction.followup.send(f"🟡 {member.mention}님에게 테스트 역할을 부여했습니다.")

        except discord.Forbidden:
            self.cog.logger.error(f"❌ 역할 부여 권한이 없습니다. 봇 권한을 확인해주세요. {traceback.format_exc()}")
            await interaction.followup.send("❌ 역할 부여 권한이 없습니다. 봇 권한을 확인해주세요.", ephemeral=True)
        except Exception as e:
            self.cog.logger.error(f"❌ 테스트 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ 오류 발생: {str(e)}", ephemeral=True)

    @discord.ui.button(label="불합격", style=discord.ButtonStyle.danger, custom_id="interview_fail")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            self.cog.logger.warning(f"관리자 권한 없는 사용자 ({interaction.user.display_name})가 불합격 버튼을 누름.")
            return await interaction.followup.send("❌ 이 버튼을 사용할 권한이 없습니다.", ephemeral=True)

        user_id = self._extract_user_id(interaction)
        if not user_id:
            self.cog.logger.warning(f"불합격 처리 시 user_id를 찾을 수 없습니다. 메시지 ID: {interaction.message.id}")
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"불합격 처리 시 멤버를 찾을 수 없습니다. User ID: {user_id}")
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )
        try:
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
                self.cog.logger.info(f"❌ {member.display_name}님에게 불합격 안내 DM 전송 완료.")
            except discord.Forbidden:
                self.cog.logger.warning(f"❌ {member.display_name} ({member.id})님에게 DM을 보낼 수 없습니다. (DM이 비활성화되었거나 차단됨)")
                await interaction.followup.send(f"❌ {member.mention}님을 불합격 처리했습니다. (DM 전송 실패: DM이 비활성화되었을 수 있습니다.)")
                return

            # --- NEW: Google Sheets Integration ---
            if self.cog.gspread_client:
                # Remove from test sheet (if they were there)
                self.cog.gspread_client.remove_from_test_sheet(member.id, member.display_name)  # No await needed here
            # --- End of Google Sheets Integration ---

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="불합격 처리로 인한 지원자 역할 제거")
                self.cog.logger.info(f"지원자 역할 '{applicant_role.name}'을(를) {member.display_name}님에게서 제거했습니다.")

            await interaction.followup.send(f"❌ {member.mention}님을 불합격 처리했습니다.")
            self.cog.logger.info(f"❌ {member.display_name} ({member.id})님을 불합격 처리했습니다.")

            # Kick the user after a short delay to allow the DM to send
            await asyncio.sleep(5)  # Give some time for the DM to be delivered
            try:
                await member.kick(reason="클랜 인터뷰 불합격")
                self.cog.logger.info(f"{member.display_name} ({member.id})님을 서버에서 추방했습니다 (불합격 처리).")
            except discord.Forbidden:
                self.cog.logger.error(f"❌ {member.display_name} ({member.id})님을 추방할 권한이 없습니다. 봇 권한을 확인해주세요.")
                await interaction.followup.send(
                    f"❌ {member.mention}님을 추방하지 못했습니다. 봇 권한을 확인해주세요.", ephemeral=True)


        except Exception as e:
            self.cog.logger.error(f"❌ 불합격 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ 오류 발생: {str(e)}", ephemeral=True)


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
        await interaction.response.defer(ephemeral=True)  # Defer the interaction

        user = interaction.user
        guild = interaction.guild

        # Store answers in the modal instance for later retrieval if needed,
        # but for embed, we'll iterate through children again.
        for item in self.children:
            self.answers[item.label] = item.value.strip()

        region = self.answers.get("활동 지역 (서부/중부/동부)", "")
        if region not in ("서부", "중부", "동부"):
            return await interaction.followup.send(  # Use followup after defer
                "❌ 올바른 활동 지역을 입력해주세요 (서부, 중부, 동부 중 하나).",
                ephemeral=True
            )

        cog = interaction.client.get_cog("InterviewRequestCog")
        if not cog:
            fallback_logger = get_logger("interview_modal_fallback")
            fallback_logger.error("❌ 인터뷰 코그를 찾을 수 없습니다. on_submit에서.")
            return await interaction.followup.send(  # Use followup after defer
                "❌ 인터뷰 코그를 찾을 수 없습니다.",
                ephemeral=True
            )

        applicant_role = guild.get_role(APPLICANT_ROLE_ID)
        guest_role = guild.get_role(GUEST_ROLE_ID)

        if not applicant_role:
            cog.logger.error(f"APPLICANT_ROLE_ID ({APPLICANT_ROLE_ID}) 역할을 찾을 수 없습니다.")
            return await interaction.followup.send("❌ 지원자 역할을 찾을 수 없습니다. 봇 설정 오류입니다.", ephemeral=True)

        if applicant_role in user.roles:
            return await interaction.followup.send("이미 인터뷰 질문을 제출하셨습니다. 관리자의 답변을 기다려주세요.", ephemeral=True)

        try:
            await user.add_roles(applicant_role, reason="인터뷰 질문 제출 완료")
            cog.logger.info(f"✅ {user.display_name} ({user.id})님에게 '지원자' 역할을 부여했습니다.")

            if guest_role and guest_role in user.roles:
                await user.remove_roles(guest_role, reason="인터뷰 질문 제출 후 '게스트' 역할 제거")
                cog.logger.info(f"✅ {user.display_name} ({user.id})님에게서 '게스트' 역할을 제거했습니다.")
        except discord.Forbidden:
            cog.logger.error(f"❌ 역할 부여/제거 권한이 없습니다. 봇 권한을 확인해주세요. {traceback.format_exc()}")
            return await interaction.followup.send("❌ 역할을 부여/제거할 권한이 없습니다. 봇 권한을 확인해주세요.", ephemeral=True)
        except Exception as e:
            cog.logger.error(f"❌ 역할 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
            return await interaction.followup.send(f"❌ 역할 처리 중 오류가 발생했습니다: {e}", ephemeral=True)

        embed = discord.Embed(
            title=f"📝 인터뷰 요청 접수",
            description=f"**<@{user.id}> 님이 인터뷰를 요청했습니다.**\n\n"
                        f"제출된 답변을 확인하고 합격, 테스트, 또는 불합격 여부를 결정해주세요.",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=user.avatar.url if user.avatar else user.default_avatar.url)
        embed.set_author(name="Exceed 인터뷰 시스템")

        # Populate embed fields from modal children
        for item in self.children:
            embed.add_field(
                name=f"❓ {item.label}",
                value=f"> {item.value.strip() or '*응답 없음*'}",
                inline=False
            )
        embed.set_footer(text=f"User ID: {user.id}")

        public_channel = guild.get_channel(cog.public_channel_id)
        private_channel = guild.get_channel(cog.private_channel_id)

        try:
            if public_channel:
                await public_channel.send(
                    content=f"{user.mention}님이 인터뷰 질문을 제출했습니다! 관리자분들의 검토가 필요합니다.",
                    embed=embed,
                    view=DecisionButtonView(applicant_id=user.id, cog=cog)
                )
                cog.logger.info(f"공개 채널에 {user.display_name}님의 인터뷰 요청 메시지 전송.")
            else:
                cog.logger.error(f"공개 채널 ID {cog.public_channel_id}를 찾을 수 없습니다.")

            if private_channel and private_channel != public_channel:  # Avoid sending twice if IDs are same
                await private_channel.send(
                    content=f"새 지원자 {user.mention} ({user.display_name})님의 인터뷰 질문이 도착했습니다. 확인 후 처리해주세요.",
                    embed=embed.copy(),  # Send a copy to avoid modifying the same embed object
                    view=DecisionButtonView(applicant_id=user.id, cog=cog)
                )
                cog.logger.info(f"비공개 채널에 {user.display_name}님의 인터뷰 요청 메시지 전송.")
            elif not private_channel:
                cog.logger.warning(f"비공개 채널 ID {cog.private_channel_id}를 찾을 수 없습니다. 비공개 채널에는 메시지를 보내지 않습니다.")

            # Confirmation message to the user
            await interaction.followup.send(
                "✅ 인터뷰 질문이 성공적으로 제출되었습니다! 관리자의 검토 후 결과를 알려드리겠습니다.",
                ephemeral=True
            )
            cog.logger.info(f"{user.display_name}님에게 인터뷰 질문 제출 확인 메시지 전송.")

        except Exception as e:
            cog.logger.error(f"인터뷰 요청 메시지 전송 실패: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ 인터뷰 요청 메시지 전송 중 오류 발생: {e}", ephemeral=True)


class InterviewView(View):
    def __init__(self, private_channel_id: int, cog):
        super().__init__(timeout=None)
        self.private_channel_id = private_channel_id
        self.cog = cog

    @discord.ui.button(label="인터뷰 요청 시작하기", style=discord.ButtonStyle.primary, custom_id="start_interview")
    async def start_interview(self, interaction: discord.Interaction, button: Button):
        # Check if user already has APPLICANT_ROLE_ID or ACCEPTED_ROLE_ID
        member = interaction.user
        applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
        accepted_role = interaction.guild.get_role(ACCEPTED_ROLE_ID)

        if accepted_role and accepted_role in member.roles:
            return await interaction.response.send_message(
                "이미 클랜 멤버이십니다. 환영합니다!",
                ephemeral=True
            )

        if applicant_role and applicant_role in member.roles:
            return await interaction.response.send_message(
                "이미 인터뷰 질문을 제출하셨습니다. 관리자의 답변을 기다려주세요.",
                ephemeral=True
            )

        modal = InterviewModal()  # No cog passed directly to modal
        await interaction.response.send_modal(modal)


class InterviewRequestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.public_channel_id = INTERVIEW_PUBLIC_CHANNEL_ID
        self.private_channel_id = INTERVIEW_PRIVATE_CHANNEL_ID

        self.logger = get_logger(
            "클랜 인터뷰",
            bot=bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )

        # NEW: Initialize GSpreadClient
        self.gspread_client = GSpreadClient(
            credentials_path=config.GSHEET_CREDENTIALS_PATH,
            members_sheet_name=config.MEMBERS_SHEET_NAME,
            test_sheet_name=config.TEST_SHEET_NAME
        )

        self.logger.info("InterviewRequestCog 초기화 완료.")

        self.FONT = None
        try:
            self.CONGRATS_BG_PATH = getattr(config, 'CONGRATS_BG_PATH', os.path.join("assets", "congrats_bg.gif"))
            FONT_PATH_CONFIG = getattr(config, 'FONT_PATH', os.path.join("assets", "fonts", "NotoSansKR-Bold.ttf"))
            self.FONT = ImageFont.truetype(FONT_PATH_CONFIG, 72)
            self.logger.info(f"폰트 로드 성공: {FONT_PATH_CONFIG}")
        except ImportError:
            self.logger.warning("Pillow ImageFont를 찾을 수 없습니다. 기본 폰트를 사용합니다.")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()
        except IOError:
            self.logger.warning(f"폰트 파일이 '{FONT_PATH_CONFIG}' 경로에서 발견되지 않았습니다. 기본 폰트를 사용합니다.")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()
        except Exception as e:
            self.logger.error(f"폰트 로드 중 알 수 없는 오류 발생: {e}\n{traceback.format_exc()}")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()

    async def make_congrats_card(self, member: discord.Member) -> Optional[BytesIO]:
        try:
            # Ensure the path is correct
            if not os.path.exists(self.CONGRATS_BG_PATH):
                self.logger.error(f"축하 배경 이미지를 찾을 수 없습니다: {self.CONGRATS_BG_PATH}. 경로를 확인하세요.")
                return None
            bg = Image.open(self.CONGRATS_BG_PATH).convert("RGBA")
        except FileNotFoundError:
            self.logger.error(f"축하 배경 이미지를 찾을 수 없습니다: {self.CONGRATS_BG_PATH}")
            return None
        except Exception as e:
            self.logger.error(f"배경 이미지 로드 중 오류 발생: {e}\n{traceback.format_exc()}")
            return None

        draw = ImageDraw.Draw(bg)

        # Use member.display_avatar.url for flexibility
        avatar_url = member.display_avatar.url
        avatar_bytes = None
        try:
            # Fetch avatar using aiohttp or similar for async operations
            async with self.bot.http_session.get(avatar_url) as resp:  # Assuming bot has aiohttp session
                if resp.status == 200:
                    avatar_bytes = await resp.read()
                else:
                    self.logger.warning(f"Failed to fetch avatar for {member.display_name}. Status: {resp.status}")
        except Exception as e:
            self.logger.error(f"❌ [congrats] {member.display_name}의 아바타 가져오기 실패: {e}\n{traceback.format_exc()}")
            avatar_bytes = None

        if avatar_bytes:
            try:
                # Create a circular avatar mask
                mask = Image.new("L", (128, 128), 0)
                draw_mask = ImageDraw.Draw(mask)
                draw_mask.ellipse((0, 0, 128, 128), fill=255)

                avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")

                # Apply the circular mask
                alpha_composite = Image.composite(avatar, Image.new("RGBA", (128, 128)), mask)

                avatar_x = 40
                avatar_y = (bg.height - alpha_composite.height) // 2
                bg.paste(alpha_composite, (avatar_x, avatar_y), alpha_composite)  # Use alpha_composite for pasting
            except Exception as e:
                self.logger.error(f"아바타 이미지 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
        else:
            self.logger.warning(f"아바타를 가져오지 못하여 {member.display_name}의 축하 카드에 아바타를 추가할 수 없습니다.")

        text = f"축하합니다, {member.display_name}님!"

        current_font = self.FONT if self.FONT else ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()

        # Get text bounding box relative to (0,0)
        text_bbox = draw.textbbox((0, 0), text, font=current_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        avatar_width_used = 128 if avatar_bytes else 0  # Account for avatar width in text positioning
        text_x = 40 + avatar_width_used + 30  # X position after avatar + padding
        text_y = (bg.height - text_height) // 2  # Center vertically

        draw.text((text_x, text_y), text, font=current_font, fill="white")

        buf = BytesIO()
        try:
            bg.save(buf, "PNG")
            buf.seek(0)
            return buf
        except Exception as e:
            self.logger.error(f"축하 카드 이미지 저장 중 오류 발생: {e}\n{traceback.format_exc()}")
            return None

    async def send_welcome_message(self, member: discord.Member):
        """Send welcome message to welcome channel"""
        channel = self.bot.get_channel(WELCOME_CHANNEL_ID)
        if not channel:
            self.logger.error(f"환영 채널 ID {WELCOME_CHANNEL_ID}을(를) 찾을 수 없습니다.")
            return

        file = None
        try:
            card_buf = await self.make_congrats_card(member)
            if card_buf:
                file = File(card_buf, filename="welcome.png")
            else:
                self.logger.warning(f"{member.display_name}님의 환영 카드 생성에 실패했습니다. 파일 없이 메시지를 보냅니다.")

            embed = discord.Embed(
                title=f"🎉 {member.display_name}님, Exceed 클랜에 합격하셨습니다!",
                description="축하드립니다! 공식 클랜 멤버가 되신 것을 진심으로 환영합니다.",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="1️⃣ 클랜 규칙을 꼭 확인해 주세요!", value=f"<#{config.RULES_CHANNEL_ID}>", inline=False)
            embed.add_field(name="2️⃣ 역할지급 채널에서 원하는 역할을 선택해 주세요.", value=f"<#{config.ROLE_ASSIGN_CHANNEL_ID}>",
                            inline=False)
            embed.add_field(name="3️⃣ 멤버 전용 채팅방을 확인해 보세요.", value=f"<#{config.MEMBER_CHAT_CHANNEL_ID}>", inline=False)
            embed.add_field(name="4️⃣ 클랜 MMR 시스템을 기반으로 한 클랜 리더보드를 확인해 보세요.",
                            value=f"<#{config.CLAN_LEADERBOARD_CHANNEL_ID}>", inline=False)

            if file:
                embed.set_image(url="attachment://welcome.png")

            embed.set_footer(text="Exceed • 합격 축하 메시지", icon_url=self.bot.user.display_avatar.url)

            await channel.send(
                content=member.mention,
                embed=embed,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True))
            self.logger.info(f"환영 메시지 전송 완료: {member.display_name} ({member.id})")

        except Exception as e:
            self.logger.error(f"환영 메시지 전송 실패: {str(e)}\n{traceback.format_exc()}")

    async def send_interview_request_message(self):
        channel = self.bot.get_channel(self.public_channel_id)
        if not channel:
            self.logger.error(f"공개 채널 ID {self.public_channel_id}를 찾을 수 없습니다.")
            return

        try:
            # Clear all messages in the channel before sending new ones
            await channel.purge(limit=None)
            self.logger.info(f"채널 #{channel.name} ({channel.id})의 기존 메시지를 삭제했습니다.")

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
            self.logger.info("📨・지원서-제출 채널에 가입 조건 안내 및 인터뷰 버튼을 게시했습니다.")

        except Exception as e:
            self.logger.error(f"인터뷰 요청 메시지 전송 실패: {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure permanent views are added so buttons work after bot restarts
        self.bot.add_view(InterviewView(self.private_channel_id, self))
        self.bot.add_view(DecisionButtonView(cog=self))  # Pass cog to DecisionButtonView

        # Initial message send/refresh
        await self.send_interview_request_message()
        self.logger.info("인터뷰 요청 메시지 및 영구 뷰 설정 완료.")

    @discord.app_commands.command(
        name="request_interview",
        description="인터뷰 요청 메시지를 다시 보냅니다 (관리자용)"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def slash_request_interview(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.send_interview_request_message()
        await interaction.followup.send(
            "인터뷰 요청 메시지를 갱신했습니다!",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(InterviewRequestCog(bot))