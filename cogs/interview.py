import asyncio
import os
from io import BytesIO

import re
import uuid # Add this import for generating unique IDs

import discord
from PIL import Image, ImageDraw, ImageFont
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput
from discord import TextStyle, File
import traceback
from datetime import datetime, timezone

from typing import Optional

from utils import config
from utils.config import INTERVIEW_PUBLIC_CHANNEL_ID, INTERVIEW_PRIVATE_CHANNEL_ID, WELCOME_CHANNEL_ID, \
    RULES_CHANNEL_ID, ANNOUNCEMENTS_CHANNEL_ID, ACCEPTED_ROLE_ID, MEMBER_CHAT_CHANNEL_ID
from utils.logger import get_logger
from utils.gspread_utils import GSpreadClient # Corrected import for Google Sheets client
from utils.config import APPLICANT_ROLE_ID, GUEST_ROLE_ID, MEMBERS_SHEET_NAME, TEST_SHEET_NAME # Ensure these are imported

class DecisionButtonView(discord.ui.View):
    def __init__(self, applicant_id: int = None, interview_id: str = None, cog=None):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.interview_id = interview_id # Store the interview ID
        self.logger = cog.logger # <--- ADD THIS LINE to pass the logger
        self.cog = cog

    def _extract_user_id_and_interview_id(self, interaction: discord.Interaction) -> tuple[Optional[int], Optional[str]]:
        user_id = None
        interview_id = None
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            # Extract user ID from description or fields
            mention_match = re.search(r'<@!?(\d+)>', embed.description or "")
            if not mention_match:
                for field in embed.fields:
                    mention_match = re.search(r'<@!?(\d+)>', field.value)
                    if mention_match:
                        break
            if mention_match:
                user_id = int(mention_match.group(1))

            # Extract interview ID from fields (assuming it's added as a field in the embed)
            for field in embed.fields:
                if field.name.strip().lower() == "❓ interview_id": # Match the field name from InterviewModal.on_submit
                    interview_id = field.value.replace('>', '').strip()
                    break
        return user_id, interview_id

    @discord.ui.button(label="합격", style=discord.ButtonStyle.success, custom_id="approve_button")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        channel = interaction.channel
        self.logger.info(f"✅ {member.display_name} ({member.id})님이 채널 '{channel.name}'에서 '합격' 버튼을 눌렀습니다.")

        if not self.cog.check_staff_role(member):
            await interaction.response.send_message("❌ 이 버튼을 사용할 권한이 없습니다.", ephemeral=True)
            self.logger.warning(f"⚠️ {member.display_name} ({member.id})님이 '합격' 버튼을 사용하려 했으나 권한이 없습니다.")
            return

        # 인터뷰 ID 추출 로직 (기존 로직 사용)
        interview_id = self.cog.extract_interview_id_from_channel_name(channel.name)
        if not interview_id:
            await interaction.response.send_message("❌ 채널 이름에서 유효한 인터뷰 ID를 찾을 수 없습니다.", ephemeral=True)
            self.logger.error(f"❌ 채널 '{channel.name}'에서 인터뷰 ID 추출 실패.")
            return

        target_member_id = self.cog.extract_user_id_from_channel_name(channel.name)
        target_member = None
        if target_member_id:
            target_member = interaction.guild.get_member(target_member_id)

        if not target_member:
            await interaction.response.send_message("❌ 이 채널에 연결된 멤버를 찾을 수 없습니다. 수동으로 처리해주세요.", ephemeral=True)
            self.logger.error(f"❌ 인터뷰 ID '{interview_id}'에 연결된 멤버를 Discord에서 찾을 수 없습니다.")
            return

        # --- Google Sheets 작업 시작 ---
        if self.cog and self.cog.gspread_client and interview_id:
            try:
                # 1. Testing 시트에서 해당 인터뷰 데이터 가져오기
                testing_worksheet = await self.cog.gspread_client.get_worksheet(config.TEST_SHEET_NAME, "Sheet1")
                if not testing_worksheet:
                    await interaction.response.send_message("❌ 'Testing' 시트에 접근할 수 없습니다.", ephemeral=True)
                    return

                all_testing_values = await asyncio.to_thread(testing_worksheet.get_all_values)
                if not all_testing_values:
                    self.logger.warning("🟡 'Testing' 시트가 비어있습니다.")
                    await interaction.response.send_message("❌ 'Testing' 시트에서 데이터를 찾을 수 없습니다.", ephemeral=True)
                    return

                header_testing = [h.strip().lower() for h in all_testing_values[0]]
                interview_data_row = None
                interview_row_index = -1

                # 'Interview_ID' 열의 인덱스 찾기
                try:
                    interview_id_col_index = header_testing.index("interview_id")
                except ValueError:
                    self.logger.error("❌ 'Testing' 시트에 'Interview_ID' 열이 없습니다.")
                    await interaction.response.send_message(
                        "❌ 'Testing' 시트의 열 구조가 올바르지 않습니다. 'Interview_ID' 열을 찾을 수 없습니다.", ephemeral=True)
                    return

                for i, row in enumerate(all_testing_values[1:]):  # 헤더 제외, 실제 데이터 행에서 검색
                    if len(row) > interview_id_col_index and row[interview_id_col_index] == interview_id:
                        interview_data_row = row
                        interview_row_index = i + 2  # Google Sheets 1-based index (header + 1 for 0-indexed list)
                        break

                if not interview_data_row:
                    self.logger.warning(f"🟡 인터뷰 ID '{interview_id}'에 해당하는 데이터를 'Testing' 시트에서 찾을 수 없습니다.")
                    await interaction.response.send_message(
                        f"❌ 인터뷰 ID '{interview_id}'에 해당하는 데이터를 'Testing' 시트에서 찾을 수 없습니다.", ephemeral=True)
                    return

                # 필요한 데이터 추출 (Testing 시트의 예상 열 순서에 따라 인덱스 조정)
                # 'Submission_Time', 'Discord_User_ID', 'Discord_Username', '활동 지역', '인게임 이름 및 태그', '가장 자신있는 역할', '프리미어 팀 참가 의향', '지원 동기', 'Status'

                # 열 이름을 기반으로 안전하게 인덱스를 가져오는 함수
                def get_column_value(row_data, header_list, column_name_lower):
                    try:
                        idx = header_list.index(column_name_lower)
                        return row_data[idx] if idx < len(row_data) else ""
                    except ValueError:
                        return ""  # 컬럼이 없으면 빈 문자열 반환

                discord_user_id = get_column_value(interview_data_row, header_testing, "discord_user_id")
                discord_username = get_column_value(interview_data_row, header_testing, "discord_username")
                ingame_name_tag = get_column_value(interview_data_row, header_testing, "인게임 이름 및 태그 (예: 이름#태그)")
                activity_region = get_column_value(interview_data_row, header_testing, "활동 지역 (서부/중부/동부)")
                main_role = get_column_value(interview_data_row, header_testing, "가장 자신있는 역할")
                premier_interest = get_column_value(interview_data_row, header_testing, "프리미어 팀 참가 의향")
                # '특이사항 또는 관리자 메모'는 현재 Testing 시트에서 직접 가져올 필드가 없으므로, 필요시 수동 입력 또는 비워둡니다.
                notes = ""  # 초기에는 비워둠

                # 2. Member List 시트에 새 항목 추가
                accepted_date = datetime.date.today().strftime("%Y-%m-%d")  # 오늘 날짜

                # Member List 시트의 열 순서에 맞춰 데이터 리스트 생성
                new_member_data = [
                    discord_user_id,
                    discord_username,
                    accepted_date,
                    ingame_name_tag,
                    activity_region,
                    main_role,
                    premier_interest,
                    notes
                ]

                member_list_sheet_name = config.MEMBERS_SHEET_NAME  # 'Member List' 시트 이름
                member_list_worksheet_name = "Sheet1"  # 'Member List' 시트 내의 워크시트 이름

                append_success = await self.cog.gspread_client.append_row(
                    member_list_sheet_name,
                    member_list_worksheet_name,
                    new_member_data
                )

                if not append_success:
                    await interaction.response.send_message("❌ 'Member List' 시트에 새 멤버를 추가하는 데 실패했습니다.", ephemeral=True)
                    return

                # 3. Testing 시트에서 해당 항목 삭제
                # delete_row_by_interview_id는 해당 ID가 속한 행을 정확히 삭제해야 합니다.
                delete_success = await self.cog.gspread_client.delete_row_by_interview_id(
                    config.TEST_SHEET_NAME,
                    "Sheet1",
                    interview_id
                )

                if not delete_success:
                    self.logger.error(f"❌ 'Testing' 시트에서 인터뷰 ID '{interview_id}' 항목 삭제 실패.")
                    await interaction.response.send_message(f"❌ 'Testing' 시트에서 항목을 삭제하는 데 실패했습니다. 수동으로 확인해주세요.",
                                                            ephemeral=True)
                    # 이 경우에도 Member List에 추가되었으므로 응답은 성공으로 간주하지만, 로그로 남깁니다.

                # --- Google Sheets 작업 종료 ---

                # 역할 부여 및 제거 (기존 로직 유지)
                # Accepted 역할 부여
                accepted_role_id = config.ACCEPTED_ROLE_ID
                accepted_role = interaction.guild.get_role(accepted_role_id)
                if accepted_role:
                    await target_member.add_roles(accepted_role, reason="합격 처리 - 역할 부여")
                    self.logger.info(
                        f"✅ {target_member.display_name} ({target_member.id})님에게 '{accepted_role.name}' 역할 부여.")
                else:
                    self.logger.warning(
                        f"⚠️ 'Accepted' 역할 (ID: {accepted_role_id})을 찾을 수 없어 {target_member.display_name}님에게 부여하지 못했습니다.")

                # Guest 및 Applicant 역할 제거 (기존 로직 유지)
                guest_role = interaction.guild.get_role(config.GUEST_ROLE_ID)
                if guest_role and guest_role in target_member.roles:
                    await target_member.remove_roles(guest_role, reason="합격 처리 - Guest 역할 제거")
                    self.logger.info(f"✅ {target_member.display_name} ({target_member.id})님에게 'Guest' 역할 제거.")

                applicant_role = interaction.guild.get_role(config.APPLICANT_ROLE_ID)
                if applicant_role and applicant_role in target_member.roles:
                    await target_member.remove_roles(applicant_role, reason="합격 처리 - Applicant 역할 제거")
                    self.logger.info(f"✅ {target_member.display_name} ({target_member.id})님에게 'Applicant' 역할 제거.")

                # 결과 메시지 전송 및 채널 삭제 (기존 로직 유지)
                await interaction.response.send_message(
                    f"✅ `{target_member.display_name}`님의 인터뷰가 합격 처리되었습니다. 'Member List'에 추가되었습니다.",
                    ephemeral=False  # 모든 사람이 볼 수 있도록
                )
                self.logger.info(f"✅ 인터뷰 ID '{interview_id}' 합격 처리 완료. 채널 삭제 대기 중.")

                # 채널 삭제 (기존 로직 유지)
                # self.cog.delete_channel_after_delay는 Discord API 요청에 포함되지 않으므로, 이 시점에서 응답을 보냅니다.
                await self.cog.delete_channel_after_delay(channel, 10, target_member.id, True)

            except Exception as e:
                self.logger.error(f"❌ 합격 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
                await interaction.response.send_message(
                    f"❌ 합격 처리 중 오류가 발생했습니다. 자세한 내용은 봇 로그를 확인해주세요.",
                    ephemeral=True
                )
                if hasattr(self.cog.bot, 'get_channel') and config.LOG_CHANNEL_ID:
                    log_channel = self.cog.bot.get_channel(config.LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(
                            f"🚨 **인터뷰 처리 오류:** 합격 처리 중 `인터뷰 ID: {interview_id}`에 대해 예상치 못한 오류 발생: `{e}`"
                        )
        else:
            await interaction.response.send_message("❌ Google Sheets 클라이언트가 초기화되지 않았거나 인터뷰 ID를 찾을 수 없습니다.",
                                                    ephemeral=True)
            self.logger.error("❌ Google Sheets 클라이언트가 없거나 인터뷰 ID가 없어 합격 처리를 진행할 수 없습니다.")

    @discord.ui.button(label="테스트", style=discord.ButtonStyle.secondary, custom_id="interview_test")
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            self.cog.logger.warning(
                f"{interaction.user.display_name} ({interaction.user.id})님이 테스트 버튼을 사용하려 했으나 권한이 없습니다.")
            return await interaction.followup.send("❌ 이 작업을 수행할 권한이 없습니다. 관리자만 사용할 수 있습니다.", ephemeral=True)

        user_id, interview_id = self._extract_user_id_and_interview_id(interaction)
        if not user_id:
            self.cog.logger.warning(f"테스트 처리 시 user_id를 찾을 수 없습니다. 메시지 ID: {interaction.message.id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"테스트 처리 시 멤버를 찾을 수 없습니다. User ID: {user_id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        try:
            # Update Google Sheet status
            if self.cog and self.cog.gspread_client and interview_id:
                success = await self.cog.gspread_client.update_row_by_interview_id(  # CHANGE THIS LINE
                    config.TEST_SHEET_NAME,
                    "Sheet1",
                    interview_id,
                    "Status",
                    "Testing"
                )
                if not success:
                    self.cog.logger.error(f"❌ Google Sheet 업데이트 실패: {user_id} 테스트 처리.")
                    await interaction.followup.send(
                        "❌ Google Sheet 업데이트에 실패했습니다. 관리자에게 문의하세요.",
                        ephemeral=True
                    )
                    return # Exit if sheet update failed

            test_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if not test_role:
                self.cog.logger.error(f"❌ 테스트 역할 ID {APPLICANT_ROLE_ID}을(를) 찾을 수 없습니다. 설정 확인 필요.")
                return await interaction.followup.send("❌ 테스트 역할을 찾을 수 없습니다.", ephemeral=True)

            await member.add_roles(test_role, reason="테스트 역할 부여 (관리자 승인)")
            self.cog.logger.info(f"🟡 {member.display_name} ({member.id})님에게 테스트 역할 '{test_role.name}'을(를) 부여했습니다.")

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

        user_id, interview_id = self._extract_user_id_and_interview_id(interaction)
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
            # Update Google Sheet status
            if self.cog and self.cog.gspread_client and interview_id:
                success = await self.cog.gspread_client.update_row_by_interview_id(  # CHANGE THIS LINE
                    config.TEST_SHEET_NAME,
                    "Sheet1",
                    interview_id,
                    "Status",
                    "Rejected"
                )
                if not success:
                    self.cog.logger.error(f"❌ Google Sheet 업데이트 실패: {user_id} 불합격 처리.")
                    await interaction.followup.send(
                        "❌ Google Sheet 업데이트에 실패했습니다. 관리자에게 문의하세요.",
                        ephemeral=True
                    )
                    return # Exit if sheet update failed

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

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="불합격 처리로 인한 지원자 역할 제거")
                self.cog.logger.info(f"지원자 역할 '{applicant_role.name}'을(를) {member.display_name}님에게서 제거했습니다.")

            await interaction.followup.send(f"❌ {member.mention}님을 불합격 처리했습니다.")
            self.cog.logger.info(f"❌ {member.display_name} ({member.id})님을 불합격 처리했습니다.")

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
        for item in self.children:
            self.answers[item.label] = item.value.strip()

        region = self.answers.get("활동 지역 (서부/중부/동부)", "")
        if region not in ("서부", "중부", "동부"):
            return await interaction.response.send_message(
                "❌ 올바른 활동 지역을 입력해주세요 (서부, 중부, 동부 중 하나).",
                ephemeral=True
            )

        cog = interaction.client.get_cog("InterviewRequestCog")
        if not cog:
            fallback_logger = get_logger("interview_modal_fallback")
            fallback_logger.error("❌ 인터뷰 코그를 찾을 수 없습니다. on_submit에서.")
            return await interaction.response.send_message(
                "❌ 인터뷰 코그를 찾을 수 없습니다.",
                ephemeral=True
            )

        private_channel = interaction.guild.get_channel(cog.private_channel_id)
        if not private_channel:
            cog.logger.error(f"❌ 비공개 채널을 찾을 수 없습니다. ID: {cog.private_channel_id}")
            return await interaction.response.send_message(
                "❌ 비공개 채널을 찾을 수 없습니다.",
                ephemeral=True
            )

        interview_id = str(uuid.uuid4()) # Generate a unique ID for this interview request
        submission_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Prepare data for Google Sheet
        sheet_data = [
            interview_id,
            submission_time,
            interaction.user.id,
            interaction.user.display_name,
            self.answers.get("활동 지역 (서부/중부/동부)", ""),
            self.answers.get("인게임 이름 및 태그 (예: 이름#태그)", ""),
            self.answers.get("가장 자신있는 역할", ""),
            self.answers.get("프리미어 팀 참가 의향", ""),
            self.answers.get("지원 동기", ""),
            "Pending" # Initial status
        ]

        # Append data to Google Sheet
        if cog.gspread_client:
            success = await cog.gspread_client.append_row(config.TEST_SHEET_NAME, "Sheet1", sheet_data)
            # Assuming "Sheet1" is the default worksheet for applications.
            # You might want to make this configurable in config.py as well if needed.
            if not success:
                cog.logger.error(f"❌ Google Sheet에 데이터 추가 실패: {interaction.user.display_name}의 인터뷰 요청.")
                await interaction.response.send_message(
                    "❌ 인터뷰 요청이 전송되었으나, Google Sheet에 기록하는 중 오류가 발생했습니다. 관리자에게 문의하세요.",
                    ephemeral=True
                )
                return

        embed = discord.Embed(
            title="📝 인터뷰 요청 접수",
            description=f"{interaction.user.mention} 님이 인터뷰를 요청했습니다.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_author(name="Exceed 인터뷰 시스템")

        # Add all answers from the modal
        for question, answer in self.answers.items():
            embed.add_field(
                name=f"❓ {question}",
                value=f"> {answer or '*응답 없음*'}",
                inline=False
            )
        embed.add_field(name="❓ Interview_ID", value=f"> {interview_id}", inline=False) # Add Interview ID to embed

        view = DecisionButtonView(applicant_id=interaction.user.id, interview_id=interview_id, cog=cog) # Pass interview_id
        await private_channel.send(embed=embed, view=view)
        cog.logger.info(f"인터뷰 요청 접수: {interaction.user.display_name} ({interaction.user.id}), Interview ID: {interview_id}")

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

        self.logger = get_logger(
            "클랜 인터뷰",
            bot=bot,
            discord_log_channel_id=config.LOG_CHANNEL_ID
        )
        self.logger.info("InterviewRequestCog 초기화 완료.")

        # Initialize Google Sheets client
        self.gspread_client = GSpreadClient(config.GSHEET_CREDENTIALS_PATH, self.logger)
        self.logger.info("Google Sheets client instance created.")

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

        # --- ADD THIS NEW METHOD ---
        def check_staff_role(self, member: discord.Member) -> bool:
            """Checks if the member has the staff role."""
            if not config.STAFF_ROLE_ID:
                self.logger.warning(
                    "⚠️ STAFF_ROLE_ID is not configured in config.py. All users will be denied staff access.")
                return False

            staff_role = member.guild.get_role(config.STAFF_ROLE_ID)
            if not staff_role:
                self.logger.error(
                    f"❌ Staff role with ID {config.STAFF_ROLE_ID} not found in guild {member.guild.name}.")
                return False

            return staff_role in member.roles
    async def make_congrats_card(self, member: discord.Member) -> Optional[BytesIO]:
        try:
            bg = Image.open(self.CONGRATS_BG_PATH).convert("RGBA")
        except FileNotFoundError:
            self.logger.error(f"축하 배경 이미지를 찾을 수 없습니다: {self.CONGRATS_BG_PATH}")
            return None
        except Exception as e:
            self.logger.error(f"배경 이미지 로드 중 오류 발생: {e}\n{traceback.format_exc()}")
            return None

        draw = ImageDraw.Draw(bg)

        avatar_asset = member.display_avatar.with_size(128).with_format("png")
        try:
            avatar_bytes = await asyncio.wait_for(avatar_asset.read(), timeout=5)
        except asyncio.TimeoutError:
            self.logger.error(f"❌ [congrats] {member.display_name}의 아바타를 가져오는 데 시간 초과.")
            avatar_bytes = None
        except Exception as e:
            self.logger.error(f"❌ [congrats] {member.display_name}의 아바타 가져오기 실패: {e}\n{traceback.format_exc()}")
            avatar_bytes = None

        if avatar_bytes:
            try:
                avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")
                avatar_x = 40
                avatar_y = (bg.height - avatar.height) // 2
                bg.paste(avatar, (avatar_x, avatar_y), avatar)
            except Exception as e:
                self.logger.error(f"아바타 이미지 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
        else:
            self.logger.warning(f"아바타를 가져오지 못하여 {member.display_name}의 축하 카드에 아바타를 추가할 수 없습니다.")

        text = f"축하합니다, {member.display_name}님!"

        current_font = self.FONT if self.FONT else ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()

        text_bbox = draw.textbbox((0, 0), text, font=current_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        avatar_width_used = 128 if avatar_bytes else 0
        text_x = 40 + avatar_width_used + 30
        text_y = (bg.height - text_height) // 2

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
        # Authorize Google Sheets client here
        auth_success = await self.gspread_client.authorize()
        if not auth_success:
            self.logger.error("❌ Google Sheets client authorization failed. Sheet operations will not work.")
            # You might want to halt the bot or disable sheet-related features here
        else:
            self.logger.info("Google Sheets client authorized on bot ready.")

        self.bot.add_view(InterviewView(self.private_channel_id, self))
        # Ensure DecisionButtonView uses the correct constructor
        self.bot.add_view(DecisionButtonView(cog=self)) # When adding persistent views, applicant_id and interview_id might be None initially
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