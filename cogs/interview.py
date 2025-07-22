import asyncio
import os
from io import BytesIO

import re

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
import utils.logger as logger_module
from utils.gspread_utils import GSpreadClient

from utils.config import APPLICANT_ROLE_ID, GUEST_ROLE_ID, GSHEET_TESTING_SPREADSHEET_NAME, \
    GSHEET_MEMBER_LIST_SPREADSHEET_NAME


class DecisionButtonView(discord.ui.View):
    def __init__(self, applicant_id: int = None, cog=None):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
        self.cog = cog

    def _extract_user_id(self, interaction: discord.Interaction) -> Optional[int]:
        user_id = None
        self.cog.logger.debug(f"메시지 ID: {interaction.message.id}에서 사용자 ID를 추출하려고 시도합니다")
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            # Try to find in description first
            mention_match = re.search(r'<@!?(\d+)>', embed.description or "")
            if mention_match:
                user_id = int(mention_match.group(1))
                self.cog.logger.debug(f"임베드 설명에서 추출된 사용자 ID: {user_id}")
            else:
                # If not in description, check fields
                for field in embed.fields:
                    mention_match = re.search(r'<@!?(\d+)>', field.value)
                    if mention_match:
                        user_id = int(mention_match.group(1))
                        self.cog.logger.debug(f"{field.name} 임베드 필드에서 추출된 사용자 ID: {user_id}")
                        break

        if user_id is None:
            self.cog.logger.warning(f"메시지 ID {interaction.message.id}에 포함된 사용자 ID를 추출할 수 없습니다")
        return user_id

    async def _get_interview_data_from_sheet(self, user_id_str: str):
        """Google Sheet에서 인터뷰 데이터를 조회합니다."""
        self.cog.logger.info(f"사용자 ID: {user_id_str}에 대한 '테스트' 시트에서 데이터를 가져오려고 합니다")
        testing_worksheet = await self.cog.gspread_client.get_worksheet(
            config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1"
        )
        if not testing_worksheet:
            self.cog.logger.error(
                f"❌ Google 스프레드시트 '{config.GSHEET_TESTING_SPREADSHEET_NAME}' 스프레드시트 또는 'Sheet1' 워크시트를 찾을 수 없습니다. (사용자 데이터 가져오기: {user_id_str})")
            return None, None

        all_test_values = await asyncio.to_thread(testing_worksheet.get_all_values)
        if not all_test_values:
            self.cog.logger.warning(f"❌ '테스트' 시트가 비어 있습니다. (사용자 데이터 가져오기: {user_id_str})")
            return None, None

        test_header = all_test_values[0]
        interview_id_col_index = -1
        self.cog.logger.debug(f"'Testing' sheet header: {test_header}")

        for i, col_name in enumerate(test_header):
            if col_name.strip() == "Interview_ID":
                interview_id_col_index = i
                break

        if interview_id_col_index == -1:
            self.cog.logger.error(
                f"❌ 'Testing' sheet does not have 'Interview_ID' column. Header: {test_header} (Get data for user: {user_id_str})")
            return None, None

        testing_row_to_process = None
        for i, row in enumerate(all_test_values[1:]):  # Skip header
            if len(row) > interview_id_col_index and row[interview_id_col_index] == user_id_str:
                testing_row_to_process = row
                self.cog.logger.info(f"✅ Found data row for Interview ID '{user_id_str}' in 'Testing' sheet.")
                break

        if not testing_row_to_process:
            existing_interview_ids = [row[interview_id_col_index] for row in all_test_values[1:] if
                                      len(row) > interview_id_col_index]
            self.cog.logger.warning(
                f"❌ Could not find Interview ID '{user_id_str}' in 'Testing' sheet. Existing Interview_IDs in sheet: {existing_interview_ids}")
            return None, None

        return testing_row_to_process, test_header

    @discord.ui.button(label="합격", style=discord.ButtonStyle.success, custom_id="interview_pass")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        user_id = self._extract_user_id(interaction)
        self.cog.logger.info(f"합격 버튼이 눌렸습니다. 추출된 사용자 ID: {user_id}")
        if not user_id:
            self.cog.logger.warning(
                f"승인 과정 중 user_id를 찾을 수 없습니다. 메시지 ID: {interaction.message.id}")
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )
        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"승인 과정 중 멤버를 찾을 수 없습니다. 사용자 ID: {user_id}")
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )

        try:
            # Step 1: Get data from "Testing" sheet
            testing_row_to_process, test_header = await self._get_interview_data_from_sheet(str(user_id))

            if not testing_row_to_process:
                # _get_interview_data_from_sheet already logs specific reason
                return await interaction.followup.send(
                    f"❌ '테스트' 시트에서 {member.mention}의 인터뷰 정보를 찾을 수 없습니다. 모달 제출 후 데이터가 기록되었는지 확인하십시오.",
                    ephemeral=True)
            if not test_header:
                return await interaction.followup.send(
                    "❌ Google Sheet '테스트' 시트에서 헤더를 가져올 수 없습니다.", ephemeral=True)

            col_map = {col: test_header.index(col) for col in test_header}

            member_data_to_append = [
                str(member.id),
                member.display_name,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                testing_row_to_process[col_map.get("인게임 이름 및 태그 (예: 이름#태그)", -1)],
                testing_row_to_process[col_map.get("활동 지역 (서부/중부/동부)", -1)],
                testing_row_to_process[col_map.get("가장 자신있는 역할", -1)],
                testing_row_to_process[col_map.get("프리미어 팀 참가 의향", -1)],
                "합격 처리됨"
            ]

            success_member_list_append = await self.cog.gspread_client.append_row(
                config.GSHEET_MEMBER_LIST_SPREADSHEET_NAME, "Sheet1",
                member_data_to_append
            )

            if not success_member_list_append:
                await interaction.followup.send(
                    "❌ '멤버 목록' 시트에 승인 정보를 추가하지 못했습니다. 관리자에게 문의하십시오.",
                    ephemeral=True)
                return

            # Step 2: Remove row from "Testing" sheet
            success_delete_testing_row = await self.cog.gspread_client.delete_row_by_interview_id(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", str(user_id)
            )
            if not success_delete_testing_row:
                self.cog.logger.warning(f"승인 후 '테스트' 시트에서 {user_id}의 행을 삭제하지 못했습니다.")
                await interaction.followup.send(
                    "⚠️ '테스트' 시트에서 인터뷰 정보를 삭제하지 못했지만 승인이 완료되었습니다. 수동으로 삭제하십시오.",
                    ephemeral=True)

            # Step 3: Discord role handling and welcome message
            role = interaction.guild.get_role(ACCEPTED_ROLE_ID)
            if not role:
                self.cog.logger.error(
                    f"❌ 합격 역할 ID {ACCEPTED_ROLE_ID}를 찾을 수 없습니다. 관리자에게 문의하십시오.")
                return await interaction.followup.send(
                    "❌ 합격 역할을 찾을 수 없습니다. 관리자에게 문의하십시오.",
                    ephemeral=True
                )

            await member.add_roles(role, reason="승인됨")
            self.cog.logger.info(f"✅ {member.display_name} ({member.id}) 승인됨. 역할 '{role.name}' 부여됨.")

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="승인으로 인해 지원자 역할 제거됨")
                self.cog.logger.info(f"{member.display_name}에서 지원자 역할 '{applicant_role.name}' 제거됨.")

            guest_role = interaction.guild.get_role(GUEST_ROLE_ID)
            if guest_role and guest_role in member.roles:
                await member.remove_roles(guest_role, reason="승인으로 인해 게스트 역할 제거됨")
                self.cog.logger.info(f"{member.display_name}에서 게스트 역할 '{guest_role.name}' 제거됨.")

            await interaction.followup.send(
                f"✅ {member.mention}이(가) 승인되었습니다!"
            )
            if self.cog:
                await self.cog.send_welcome_message(member)

        except discord.Forbidden:
            self.cog.logger.error(
                f"❌ 역할 할당 권한이 없습니다. 봇 권한을 확인하십시오. {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 역할 할당 권한이 없습니다. 봇 권한을 확인하십시오.",
                ephemeral=True
            )
        except Exception as e:
            self.cog.logger.error(f"❌ 승인 과정 중 오류 발생: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(
                f"❌ 오류가 발생했습니다: {str(e)}",
                ephemeral=True
            )

    @discord.ui.button(label="테스트", style=discord.ButtonStyle.secondary, custom_id="interview_test")
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            self.cog.logger.warning(
                f"{interaction.user.display_name} ({interaction.user.id})이(가) 권한 없이 테스트 버튼을 사용하려고 시도했습니다.")
            return await interaction.followup.send(
                "❌ 이 작업을 수행할 권한이 없습니다. 관리자만 사용할 수 있습니다.",
                ephemeral=True)

        user_id = self._extract_user_id(interaction)
        self.cog.logger.info(f"테스트 버튼이 눌렸습니다. 추출된 사용자 ID: {user_id}")
        if not user_id:
            self.cog.logger.warning(
                f"테스트 처리 중 user_id를 찾을 수 없습니다. 메시지 ID: {interaction.message.id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"테스트 처리 중 멤버를 찾을 수 없습니다. 사용자 ID: {user_id}")
            return await interaction.followup.send("❌ 지원자 정보를 찾을 수 없습니다.", ephemeral=True)

        try:
            # Retrieve the existing data from the "Testing" sheet.
            # This is key: the modal *should* have already sent the initial data.
            # The 'test' button *updates* that record.
            testing_row_data, test_sheet_header = await self._get_interview_data_from_sheet(str(user_id))

            if not testing_row_data or not test_sheet_header:
                self.cog.logger.error(
                    f"테스트 처리 중 '테스트' 시트에서 {member.display_name}의 인터뷰 답변을 찾거나 시트 헤더를 검색하지 못했습니다. 사용자 ID: {user_id}")
                return await interaction.followup.send(
                    f"❌ {member.mention}의 인터뷰 답변 정보를 찾을 수 없습니다. (Google Sheet에 데이터가 기록되지 않았거나 헤더 문제). 인터뷰 요청을 다시 시도하십시오.",
                    ephemeral=True
                )

            header_indices = {col: test_sheet_header.index(col) for col in test_sheet_header}

            # Create a mutable copy and update the Status
            updated_row_data = list(testing_row_data)
            status_col_index = header_indices.get("Status", -1)

            if status_col_index != -1 and len(updated_row_data) > status_col_index:
                updated_row_data[status_col_index] = "테스트"
            elif status_col_index != -1:  # If column exists but row is too short
                updated_row_data.extend([''] * (status_col_index - len(updated_row_data) + 1))
                updated_row_data[status_col_index] = "테스트"
            else:
                self.cog.logger.warning(
                    f"❌ '테스트' 시트에 '상태' 열이 없거나 인덱스 문제입니다. {user_id}의 상태를 업데이트하지 못했습니다.")
                # Decide how to proceed if Status column isn't found/updatable
                # For now, will proceed without updating status in sheet if column is truly missing

            # Delete the old row and append the updated row to effectively "update" it
            success_delete = await self.cog.gspread_client.delete_row_by_interview_id(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", str(user_id)
            )
            if not success_delete:
                self.cog.logger.warning(
                    f"테스트 처리 중 {user_id}에 대한 기존 '테스트' 시트 행을 삭제하지 못했습니다 (존재하지 않았거나 오류). 새 항목을 추가하려고 시도합니다.")

            success_append = await self.cog.gspread_client.append_row(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", updated_row_data
            )

            if not success_append:
                await interaction.followup.send("❌ Google Sheets에 데이터를 업데이트/추가하지 못했습니다.", ephemeral=True)
                return

            test_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if not test_role:
                self.cog.logger.error(f"❌ 테스트 역할 ID {APPLICANT_ROLE_ID}를 찾을 수 없습니다. 구성 확인이 필요합니다.")
                return await interaction.followup.send("❌ 테스트 역할을 찾을 수 없습니다.", ephemeral=True)

            await member.add_roles(test_role, reason="테스트 역할 부여 (관리자 승인)")
            self.cog.logger.info(f"🟡 {member.display_name} ({member.id})에게 테스트 역할 '{test_role.name}' 부여됨.")

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
                self.cog.logger.info(f"🟡 {member.display_name}에게 테스트 안내 DM 전송됨.")
            except discord.Forbidden:
                self.cog.logger.warning(
                    f"🟡 {member.display_name} ({member.id})에게 DM을 보낼 수 없습니다. (DM 비활성화 또는 차단됨)")
                await interaction.followup.send(
                    f"🟡 {member.mention}에게 테스트 역할이 부여되었습니다. (DM 실패: DM이 비활성화되었을 수 있습니다.)")
                return

            await interaction.followup.send(f"🟡 {member.mention}에게 테스트 역할이 부여되었습니다.")

        except discord.Forbidden:
            self.cog.logger.error(
                f"❌ 역할 할당 권한이 없습니다. 봇 권한을 확인하십시오. {traceback.format_exc()}")
            await interaction.followup.send("❌ 역할 할당 권한이 없습니다. 봇 권한을 확인하십시오.",
                                            ephemeral=True)
        except Exception as e:
            self.cog.logger.error(f"❌ 테스트 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {str(e)}", ephemeral=True)

    @discord.ui.button(label="불합격", style=discord.ButtonStyle.danger, custom_id="interview_fail")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        user_id = self._extract_user_id(interaction)
        self.cog.logger.info(f"불합격 버튼이 눌렸습니다. 추출된 사용자 ID: {user_id}")
        if not user_id:
            self.cog.logger.warning(
                f"거부 처리 중 user_id를 찾을 수 없습니다. 메시지 ID: {interaction.message.id}")
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"거부 처리 중 멤버를 찾을 수 없습니다. 사용자 ID: {user_id}")
            return await interaction.followup.send(
                "❌ 지원자 정보를 찾을 수 없습니다.",
                ephemeral=True
            )
        try:
            # If rejected, remove from "Testing" sheet if they were there
            success_delete_testing_row = await self.cog.gspread_client.delete_row_by_interview_id(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", str(user_id)
            )
            if not success_delete_testing_row:
                self.cog.logger.warning(
                    f"거부 중 '테스트' 시트에서 {user_id}의 행을 삭제하지 못했습니다 (존재하지 않았거나 오류).")
            else:
                self.cog.logger.info(f"거부로 인해 '테스트' 시트에서 {user_id}의 행이 삭제되었습니다.")

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
                self.cog.logger.info(f"❌ {member.display_name}에게 거부 DM 전송됨.")
            except discord.Forbidden:
                self.cog.logger.warning(
                    f"❌ {member.display_name} ({member.id})에게 DM을 보낼 수 없습니다. (DM 비활성화 또는 차단됨)")
                await interaction.followup.send(f"❌ {member.mention}이(가) 거부되었습니다. (DM 실패: DM이 비활성화되었을 수 있습니다.)")
                return

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="불합격 처리로 인한 지원자 역할 제거")
                self.cog.logger.info(f"{member.display_name}에서 지원자 역할 '{applicant_role.name}' 제거됨.")

            await interaction.followup.send(f"❌ {member.mention}이(가) 거부되었습니다.")
            self.cog.logger.info(f"❌ {member.display_name} ({member.id})이(가) 거부되었습니다.")

        except Exception as e:
            self.cog.logger.error(f"❌ 거부 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {str(e)}", ephemeral=True)


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

        # Ensure the data is immediately sent to the 'Testing' sheet upon modal submission
        try:
            submission_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            data_row = [
                str(interaction.user.id),  # Interview_ID
                submission_time,  # Submission_Time
                str(interaction.user.id),  # Discord_User_ID
                interaction.user.display_name,  # Discord_Username
                self.answers.get("활동 지역 (서부/중부/동부)", ""),
                self.answers.get("인게임 이름 및 태그 (예: 이름#태그)", ""),
                self.answers.get("가장 자신있는 역할", ""),
                self.answers.get("프리미어 팀 참가 의향", ""),
                self.answers.get("지원 동기", ""),
                "제출됨"  # Initial Status after submission
            ]
            cog.logger.info(f"사용자: {interaction.user.id}에 대한 행을 '테스트' 시트에 추가하려고 시도합니다.")
            success = await cog.gspread_client.append_row(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", data_row
            )
            if not success:
                cog.logger.error(
                    f"❌ InterviewModal에서 사용자: {interaction.user.id}의 '테스트' 시트에 데이터를 추가하지 못했습니다. GSpreadClient에서 False를 반환했습니다.")
                return await interaction.response.send_message(
                    "❌ 인터뷰 정보를 Google Sheet에 저장하는 데 실패했습니다. 다시 시도해주세요.",
                    ephemeral=True
                )
            cog.logger.info(f"✅ 인터뷰 데이터가 '테스트' 시트에 성공적으로 저장되었습니다: {interaction.user.id}")

        except Exception as e:
            cog.logger.error(
                f"❌ InterviewModal에서 Google Sheet 데이터를 저장하는 중 오류 발생: {e}\n{traceback.format_exc()}")
            return await interaction.response.send_message(
                f"❌ 인터뷰 데이터를 처리하는 중 오류가 발생했습니다: {str(e)}",
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
        cog.logger.info(f"인터뷰 요청 접수: {interaction.user.display_name} ({interaction.user.id})")

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

        self.logger = logger_module.get_logger(self.__class__.__name__)
        self.logger.info("인터뷰 요청 기능이 초기화되었습니다.")
        self.logger = get_logger("interview_cog")
        self.private_channel_id = INTERVIEW_PRIVATE_CHANNEL_ID
        self.gspread_client = GSpreadClient(config.GSHEET_CREDENTIALS_PATH, self.logger)

    async def cog_load(self):
        await self.gspread_client.authorize()

        self.FONT = None
        try:
            self.CONGRATS_BG_PATH = getattr(config, 'CONGRATS_BG_PATH', os.path.join("assets", "congrats_bg.gif"))
            FONT_PATH_CONFIG = getattr(config, 'FONT_PATH', os.path.join("assets", "fonts", "NotoSansKR-Bold.ttf"))
            self.FONT = ImageFont.truetype(FONT_PATH_CONFIG, 72)
            self.logger.info(f"글꼴 로드 성공: {FONT_PATH_CONFIG}")
        except ImportError:
            self.logger.warning("Pillow ImageFont를 찾을 수 없습니다. 기본 글꼴을 사용합니다.")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()
        except IOError:
            self.logger.warning(f"글꼴 파일을 찾을 수 없습니다: '{FONT_PATH_CONFIG}'. 기본 글꼴을 사용합니다.")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()
        except Exception as e:
            self.logger.error(f"글꼴 로드 중 알 수 없는 오류 발생: {e}\n{traceback.format_exc()}")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()

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
        img_width, img_height = bg.size  # Get the width and height of the background image

        avatar_asset = member.display_avatar.with_size(128).with_format("png")
        try:
            avatar_bytes = await asyncio.wait_for(avatar_asset.read(), timeout=5)
        except asyncio.TimeoutError:
            self.logger.error(f"❌ [축하] {member.display_name}의 아바타를 가져오는 중 시간 초과되었습니다.")
            avatar_bytes = None
        except Exception as e:
            self.logger.error(
                f"❌ [축하] {member.display_name}의 아바타를 가져오지 못했습니다: {e}\n{traceback.format_exc()}")
            avatar_bytes = None

        if avatar_bytes:
            try:
                avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")
                avatar_size = 128  # Define avatar size for consistent centering
                avatar_x, avatar_y = None, None

                if avatar_bytes:  # Keep the existing 'if avatar_bytes' check
                    try:
                        avatar = Image.open(BytesIO(avatar_bytes)).resize((avatar_size, avatar_size)).convert("RGBA")
                        # Calculate avatar position to be centered horizontally within its "section"
                        # For vertical centering, it will be in the middle of the image.
                        avatar_x = (img_width - avatar_size) // 2
                        avatar_y = (img_height // 2) - (avatar_size // 2) - 50  # Adjusted slightly higher for spacing

                        # Create a circular mask for the avatar
                        mask = Image.new("L", avatar.size, 0)
                        mask_draw = ImageDraw.Draw(mask)
                        mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)

                        # Apply the mask
                        masked_avatar = Image.composite(avatar, Image.new("RGBA", avatar.size, (0, 0, 0, 0)), mask)

                        bg.paste(masked_avatar, (avatar_x, avatar_y), masked_avatar)
                    except Exception as e:
                        self.logger.error(f"아바타 이미지 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
                else:
                    self.logger.warning(
                        f"아바타를 가져오지 못하여 {member.display_name}님의 축하 카드에 아바타를 추가할 수 없습니다.")
            except Exception as e:
                self.logger.error(f"아바타 이미지 처리 중 오류 발생: {e}\n{traceback.format_exc()}")
        else:
            self.logger.warning(
                f"아바타를 가져오지 못하여 {member.display_name}님의 축하 카드에 아바타를 추가할 수 없습니다.")

        text = f"축하합니다, {member.display_name}님!"  # Reverted to Korean

        current_font = self.FONT if self.FONT else ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()

        # Calculate text bounding box to get its width and height
        text_bbox = draw.textbbox((0, 0), text, font=current_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]

        # Calculate text position to be centered below the avatar or in the middle if no avatar
        text_x = img_width // 2  # Changed to explicitly center horizontally

        # Position text below the avatar, with some padding, or in the middle if no avatar
        if avatar_y is not None:
            text_y = avatar_y + avatar_size + 20  # 20 pixels padding below avatar
        else:
            text_y = (img_height - text_height) // 2  # Center vertically if no avatar

        draw.text((text_x, text_y), text, font=current_font, fill="white",
                  anchor="mm")  # anchor="mm" aligns the text middle-middle to the coordinate

        buf = BytesIO()
        try:
            bg.save(buf, "PNG")
            buf.seek(0)
            return buf
        except Exception as e:
            self.logger.error(f"축하 카드 이미지 저장 중 오류 발생: {e}\n{traceback.format_exc()}")
            return None

    async def send_welcome_message(self, member: discord.Member):
        """환영 메시지를 환영 채널로 보냅니다"""
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
                # START OF CHANGES
                title=f"{member.display_name}님, Exceed 클랜에 합격하셨습니다!",  # Modified: Matches welcome message title
                description="축하드립니다! 공식 클랜 멤버가 되신 것을 진심으로 환영합니다.",  # Modified: Matches welcome message description
                # END OF CHANGES
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )
            # START OF CHANGES
            embed.set_author(name=member.display_name,
                             icon_url=member.display_avatar.url)  # Added: For top-left user icon
            embed.set_thumbnail(url=member.display_avatar.url)  # Already existed, but included for context
            # END OF CHANGES

            embed.add_field(name="1️⃣ 클랜 규칙을 꼭 확인해 주세요!", value=f"<#{config.RULES_CHANNEL_ID}>",
                            inline=False)
            # REMOVE THIS DUPLICATE LINE:
            # embed.add_field(name="1️⃣ 클랜 규칙을 꼭 확인해 주세요!", value=f"<#{config.RULES_CHANNEL_ID}>",
            #                 inline=False)  # Reverted to Korean
            embed.add_field(name="2️⃣ 역할지급 채널에서 원하는 역할을 선택해 주세요.", value=f"<#{config.ROLE_ASSIGN_CHANNEL_ID}>",
                            inline=False)
            embed.add_field(name="3️⃣ 멤버 전용 채팅방을 확인해 보세요.", value=f"<#{config.MEMBER_CHAT_CHANNEL_ID}>",
                            inline=False)
            embed.add_field(name="4️⃣ 클랜 MMR 시스템을 기반으로 한 클랜 리더보드를 확인해 보세요.",
                            value=f"<#{config.CLAN_LEADERBOARD_CHANNEL_ID}>",
                            inline=False)  # Complete this line if it was cut off

            if file:
                embed.set_image(url="attachment://welcome.png")

            embed.set_footer(text="Exceed • 합격 축하 메시지", icon_url=self.bot.user.display_avatar.url)

            await channel.send(
                content=f"{member.mention}",
                embed=embed,
                file=file
            )
            self.logger.info(f"✅ {member.display_name}에게 환영 메시지가 전송되었습니다.")
        except Exception as e:
            self.logger.error(f"환영 메시지를 보내는 중 오류 발생: {e}\n{traceback.format_exc()}")


async def setup(bot):
    await bot.add_cog(InterviewRequestCog(bot))
    bot.add_view(InterviewView(INTERVIEW_PRIVATE_CHANNEL_ID, bot.get_cog("InterviewRequestCog")))
    bot.add_view(DecisionButtonView(cog=bot.get_cog("InterviewRequestCog")))