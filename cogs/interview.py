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
        self.cog.logger.debug(f"Attempting to extract user ID from message ID: {interaction.message.id}")
        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
            # Try to find in description first
            mention_match = re.search(r'<@!?(\d+)>', embed.description or "")
            if mention_match:
                user_id = int(mention_match.group(1))
                self.cog.logger.debug(f"User ID extracted from embed description: {user_id}")
            else:
                # If not in description, check fields
                for field in embed.fields:
                    mention_match = re.search(r'<@!?(\d+)>', field.value)
                    if mention_match:
                        user_id = int(mention_match.group(1))
                        self.cog.logger.debug(f"User ID extracted from embed field '{field.name}': {user_id}")
                        break

        if user_id is None:
            self.cog.logger.warning(f"Could not extract user ID from embed in message ID: {interaction.message.id}")
        return user_id

    async def _get_interview_data_from_sheet(self, user_id_str: str):
        """Google Sheet에서 인터뷰 데이터를 조회합니다."""
        self.cog.logger.info(f"Trying to fetch data from 'Testing' sheet for user ID: {user_id_str}")
        testing_worksheet = await self.cog.gspread_client.get_worksheet(
            config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1"
        )
        if not testing_worksheet:
            self.cog.logger.error(
                f"❌ Google Sheets '{config.GSHEET_TESTING_SPREADSHEET_NAME}' spreadsheet or 'Sheet1' worksheet not found. (Get data for user: {user_id_str})")
            return None, None

        all_test_values = await asyncio.to_thread(testing_worksheet.get_all_values)
        if not all_test_values:
            self.cog.logger.warning(f"❌ 'Testing' sheet is empty. (Get data for user: {user_id_str})")
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
        self.cog.logger.info(f"Approve button pressed. Extracted user ID: {user_id}")
        if not user_id:
            self.cog.logger.warning(
                f"Could not find user_id during approval process. Message ID: {interaction.message.id}")
            return await interaction.followup.send(
                "❌ Applicant information not found.",
                ephemeral=True
            )
        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"Could not find member during approval process. User ID: {user_id}")
            return await interaction.followup.send(
                "❌ Applicant information not found.",
                ephemeral=True
            )

        try:
            # Step 1: Get data from "Testing" sheet
            testing_row_to_process, test_header = await self._get_interview_data_from_sheet(str(user_id))

            if not testing_row_to_process:
                # _get_interview_data_from_sheet already logs specific reason
                return await interaction.followup.send(
                    f"❌ Interview information for {member.mention} not found in 'Testing' sheet. Please ensure data was recorded after modal submission.",
                    ephemeral=True)
            if not test_header:
                return await interaction.followup.send(
                    "❌ Could not retrieve header from Google Sheets 'Testing' sheet.", ephemeral=True)

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
                    "❌ Failed to add approval information to 'Member List' sheet. Please contact an administrator.",
                    ephemeral=True)
                return

            # Step 2: Remove row from "Testing" sheet
            success_delete_testing_row = await self.cog.gspread_client.delete_row_by_interview_id(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", str(user_id)
            )
            if not success_delete_testing_row:
                self.cog.logger.warning(f"Failed to delete row for {user_id} from 'Testing' sheet after approval.")
                await interaction.followup.send(
                    "⚠️ Failed to delete interview information from 'Testing' sheet, but approval was completed. Please delete manually.",
                    ephemeral=True)

            # Step 3: Discord role handling and welcome message
            role = interaction.guild.get_role(ACCEPTED_ROLE_ID)
            if not role:
                self.cog.logger.error(
                    f"❌ Accepted role ID {ACCEPTED_ROLE_ID} not found. Please contact an administrator.")
                return await interaction.followup.send(
                    "❌ Accepted role not found. Please contact an administrator.",
                    ephemeral=True
                )

            await member.add_roles(role, reason="Approved")
            self.cog.logger.info(f"✅ Approved {member.display_name} ({member.id}). Role '{role.name}' granted.")

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="Applicant role removed due to approval")
                self.cog.logger.info(f"Removed applicant role '{applicant_role.name}' from {member.display_name}.")

            guest_role = interaction.guild.get_role(GUEST_ROLE_ID)
            if guest_role and guest_role in member.roles:
                await member.remove_roles(guest_role, reason="Guest role removed due to approval")
                self.cog.logger.info(f"Removed guest role '{guest_role.name}' from {member.display_name}.")

            await interaction.followup.send(
                f"✅ {member.mention} has been approved!"
            )
            if self.cog:
                await self.cog.send_welcome_message(member)

        except discord.Forbidden:
            self.cog.logger.error(
                f"❌ Missing permissions to assign roles. Please check bot permissions. {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ Missing permissions to assign roles. Please check bot permissions.",
                ephemeral=True
            )
        except Exception as e:
            self.cog.logger.error(f"❌ Error during approval process: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(
                f"❌ An error occurred: {str(e)}",
                ephemeral=True
            )

    @discord.ui.button(label="테스트", style=discord.ButtonStyle.secondary, custom_id="interview_test")
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            self.cog.logger.warning(
                f"{interaction.user.display_name} ({interaction.user.id}) attempted to use the test button without permission.")
            return await interaction.followup.send(
                "❌ You do not have permission to perform this action. Only administrators can use this.",
                ephemeral=True)

        user_id = self._extract_user_id(interaction)
        self.cog.logger.info(f"Test button pressed. Extracted user ID: {user_id}")
        if not user_id:
            self.cog.logger.warning(
                f"Could not find user_id during test processing. Message ID: {interaction.message.id}")
            return await interaction.followup.send("❌ Applicant information not found.", ephemeral=True)

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"Could not find member during test processing. User ID: {user_id}")
            return await interaction.followup.send("❌ Applicant information not found.", ephemeral=True)

        try:
            # Retrieve the existing data from the "Testing" sheet.
            # This is key: the modal *should* have already sent the initial data.
            # The 'test' button *updates* that record.
            testing_row_data, test_sheet_header = await self._get_interview_data_from_sheet(str(user_id))

            if not testing_row_data or not test_sheet_header:
                self.cog.logger.error(
                    f"Failed to find interview answers for {member.display_name} in 'Testing' sheet or retrieve sheet header during test processing. User ID: {user_id}")
                return await interaction.followup.send(
                    f"❌ Interview answer information not found for {member.mention}. (Data might not have been recorded in Google Sheet or header issue). Please try the interview request again.",
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
                    f"❌ 'Status' column missing or index issue in 'Testing' sheet. Failed to update status for {user_id}.")
                # Decide how to proceed if Status column isn't found/updatable
                # For now, will proceed without updating status in sheet if column is truly missing

            # Delete the old row and append the updated row to effectively "update" it
            success_delete = await self.cog.gspread_client.delete_row_by_interview_id(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", str(user_id)
            )
            if not success_delete:
                self.cog.logger.warning(
                    f"Failed to delete existing 'Testing' sheet row for {user_id} during test processing (might not have existed or error). Attempting to append new.")

            success_append = await self.cog.gspread_client.append_row(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", updated_row_data
            )

            if not success_append:
                await interaction.followup.send("❌ Failed to update/add data to Google Sheets.", ephemeral=True)
                return

            test_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if not test_role:
                self.cog.logger.error(f"❌ Test role ID {APPLICANT_ROLE_ID} not found. Configuration check needed.")
                return await interaction.followup.send("❌ Test role not found.", ephemeral=True)

            await member.add_roles(test_role, reason="테스트 역할 부여 (관리자 승인)")
            self.cog.logger.info(f"🟡 Granted test role '{test_role.name}' to {member.display_name} ({member.id}).")

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
                self.cog.logger.info(f"🟡 Sent test guidance DM to {member.display_name}.")
            except discord.Forbidden:
                self.cog.logger.warning(
                    f"🟡 Could not send DM to {member.display_name} ({member.id}). (DM disabled or blocked)")
                await interaction.followup.send(
                    f"🟡 Granted test role to {member.mention}. (DM failed: DM might be disabled.)")
                return

            await interaction.followup.send(f"🟡 Granted test role to {member.mention}.")

        except discord.Forbidden:
            self.cog.logger.error(
                f"❌ Missing permissions to assign roles. Please check bot permissions. {traceback.format_exc()}")
            await interaction.followup.send("❌ Missing permissions to assign roles. Please check bot permissions.",
                                            ephemeral=True)
        except Exception as e:
            self.cog.logger.error(f"❌ Error during test processing: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

    @discord.ui.button(label="불합격", style=discord.ButtonStyle.danger, custom_id="interview_fail")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        user_id = self._extract_user_id(interaction)
        self.cog.logger.info(f"Reject button pressed. Extracted user ID: {user_id}")
        if not user_id:
            self.cog.logger.warning(
                f"Could not find user_id during rejection process. Message ID: {interaction.message.id}")
            return await interaction.followup.send(
                "❌ Applicant information not found.",
                ephemeral=True
            )

        member = interaction.guild.get_member(user_id)
        if not member:
            self.cog.logger.warning(f"Could not find member during rejection process. User ID: {user_id}")
            return await interaction.followup.send(
                "❌ Applicant information not found.",
                ephemeral=True
            )
        try:
            # If rejected, remove from "Testing" sheet if they were there
            success_delete_testing_row = await self.cog.gspread_client.delete_row_by_interview_id(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", str(user_id)
            )
            if not success_delete_testing_row:
                self.cog.logger.warning(
                    f"Failed to delete row for {user_id} from 'Testing' sheet during rejection (might not have existed or error).")
            else:
                self.cog.logger.info(f"Deleted row for {user_id} from 'Testing' sheet due to rejection.")

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
                self.cog.logger.info(f"❌ Sent rejection DM to {member.display_name}.")
            except discord.Forbidden:
                self.cog.logger.warning(
                    f"❌ Could not send DM to {member.display_name} ({member.id}). (DM disabled or blocked)")
                await interaction.followup.send(f"❌ Rejected {member.mention}. (DM failed: DM might be disabled.)")
                return

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="불합격 처리로 인한 지원자 역할 제거")
                self.cog.logger.info(f"Removed applicant role '{applicant_role.name}' from {member.display_name}.")

            await interaction.followup.send(f"❌ Rejected {member.mention}.")
            self.cog.logger.info(f"❌ Rejected {member.display_name} ({member.id}).")

        except Exception as e:
            self.cog.logger.error(f"❌ Error during rejection process: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)


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
            cog.logger.info(f"Attempting to append row to 'Testing' sheet for user: {interaction.user.id}")
            success = await cog.gspread_client.append_row(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", data_row
            )
            if not success:
                cog.logger.error(
                    f"❌ Failed to add data to 'Testing' sheet from InterviewModal for user: {interaction.user.id}. GSpreadClient returned False.")
                return await interaction.response.send_message(
                    "❌ 인터뷰 정보를 Google Sheet에 저장하는 데 실패했습니다. 다시 시도해주세요.",
                    ephemeral=True
                )
            cog.logger.info(f"✅ Interview data successfully saved to 'Testing' sheet: {interaction.user.id}")

        except Exception as e:
            cog.logger.error(
                f"❌ Error while saving Google Sheet data from InterviewModal: {e}\n{traceback.format_exc()}")
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
        self.logger.info("InterviewRequestCog 초기화 완료.")
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
            self.logger.info(f"Font loaded successfully: {FONT_PATH_CONFIG}")
        except ImportError:
            self.logger.warning("Pillow ImageFont not found. Using default font.")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()
        except IOError:
            self.logger.warning(f"Font file not found at '{FONT_PATH_CONFIG}'. Using default font.")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()
        except Exception as e:
            self.logger.error(f"Unknown error occurred while loading font: {e}\n{traceback.format_exc()}")
            self.FONT = ImageDraw.Draw(Image.new('RGBA', (1, 1))).getfont()

    async def make_congrats_card(self, member: discord.Member) -> Optional[BytesIO]:
        try:
            bg = Image.open(self.CONGRATS_BG_PATH).convert("RGBA")
        except FileNotFoundError:
            self.logger.error(f"Congratulatory background image not found: {self.CONGRATS_BG_PATH}")
            return None
        except Exception as e:
            self.logger.error(f"Error loading background image: {e}\n{traceback.format_exc()}")
            return None

        draw = ImageDraw.Draw(bg)

        avatar_asset = member.display_avatar.with_size(128).with_format("png")
        try:
            avatar_bytes = await asyncio.wait_for(avatar_asset.read(), timeout=5)
        except asyncio.TimeoutError:
            self.logger.error(f"❌ [congrats] Timeout fetching avatar for {member.display_name}.")
            avatar_bytes = None
        except Exception as e:
            self.logger.error(
                f"❌ [congrats] Failed to fetch avatar for {member.display_name}: {e}\n{traceback.format_exc()}")
            avatar_bytes = None

        if avatar_bytes:
            try:
                avatar = Image.open(BytesIO(avatar_bytes)).resize((128, 128)).convert("RGBA")
                avatar_x = 40
                avatar_y = (bg.height - avatar.height) // 2
                bg.paste(avatar, (avatar_x, avatar_y), avatar)
            except Exception as e:
                self.logger.error(f"Error processing avatar image: {e}\n{traceback.format_exc()}")
        else:
            self.logger.warning(
                f"Failed to fetch avatar, cannot add avatar to {member.display_name}'s congratulatory card.")

        text = f"축하합니다, {member.display_name}님!"  # Reverted to Korean

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
            self.logger.error(f"Error saving congratulatory card image: {e}\n{traceback.format_exc()}")
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
                title=f"🎉 {member.display_name}님, Exceed 클랜에 합격하셨습니다!",  # Reverted to Korean
                description="축하드립니다! 공식 클랜 멤버가 되신 것을 진심으로 환영합니다.",  # Reverted to Korean
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="1️⃣ 클랜 규칙을 꼭 확인해 주세요!", value=f"<#{config.RULES_CHANNEL_ID}>",
                            inline=False)  # Reverted to Korean
            embed.add_field(name="2️⃣ 역할지급 채널에서 원하는 역할을 선택해 주세요.", value=f"<#{config.ROLE_ASSIGN_CHANNEL_ID}>",
                            # Reverted to Korean
                            inline=False)
            embed.add_field(name="3️⃣ 멤버 전용 채팅방을 확인해 보세요.", value=f"<#{config.MEMBER_CHAT_CHANNEL_ID}>",
                            inline=False)  # Reverted to Korean
            embed.add_field(name="4️⃣ 클랜 MMR 시스템을 기반으로 한 클랜 리더보드를 확인해 보세요.",  # Reverted to Korean
                            value=f"<#{config.CLAN_LEADERBOARD_CHANNEL_ID}>", inline=False)

            if file:
                embed.set_image(url="attachment://welcome.png")

            embed.set_footer(text="Exceed • 합격 축하 메시지", icon_url=self.bot.user.display_avatar.url)  # Reverted to Korean

            await channel.send(
                content=member.mention,
                embed=embed,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True))
            self.logger.info(f"환영 메시지 전송 완료: {member.display_name} ({member.id})")  # Reverted to Korean

        except Exception as e:
            self.logger.error(f"환영 메시지 전송 실패: {str(e)}\n{traceback.format_exc()}")  # Reverted to Korean

    async def send_interview_request_message(self):
        channel = self.bot.get_channel(self.public_channel_id)
        if not channel:
            self.logger.error(f"공개 채널 ID {self.public_channel_id}를 찾을 수 없습니다.")  # Reverted to Korean
            return

        try:
            await channel.purge(limit=None)
            self.logger.info(f"채널 #{channel.name} ({channel.id})의 기존 메시지를 삭제했습니다.")  # Reverted to Korean

            rules_embed = discord.Embed(
                title="🎯 XCD 발로란트 클랜 가입 조건 안내",  # Reverted to Korean
                description="📜 최종 업데이트: 2025.07.06",  # Reverted to Korean
                color=discord.Color.orange()
            )
            rules_embed.add_field(
                name="가입 전 아래 조건을 반드시 확인해 주세요.",  # Reverted to Korean
                value=(
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "🔞 1. 나이 조건\n"  # Reverted to Korean
                    "・만 20세 이상 (2005년생 이전)\n"  # Reverted to Korean
                    "・성숙한 커뮤니케이션과 책임감 있는 행동을 기대합니다.\n\n"  # Reverted to Korean
                    "🎮 2. 실력 조건\n"  # Reverted to Korean
                    "・현재 티어 골드 이상 (에피소드 기준)\n"  # Reverted to Korean
                    "・트라이아웃(스크림 테스트)으로 실력 확인 가능\n"  # Reverted to Korean
                    "・게임 이해도 & 팀워크도 함께 평가\n\n"  # Reverted to Korean
                    "💬 3. 매너 & 소통\n"  # Reverted to Korean
                    "・욕설/무시/조롱/반말 등 비매너 언행 금지\n"  # Reverted to Korean
                    "・피드백을 받아들이고 긍정적인 태도로 게임 가능\n"  # Reverted to Korean
                    "・디스코드 마이크 필수\n\n"  # Reverted to Korean
                    "⏱️ 4. 활동성\n"  # Reverted to Korean
                    "・주 3회 이상 접속 & 게임 참여 가능자\n"  # Reverted to Korean
                    "・대회/스크림/내전 등 일정에 적극 참여할 의향 있는 분\n"  # Reverted to Korean
                    "・30일 이상 미접속 시 자동 탈퇴 처리 가능\n\n"  # Reverted to Korean
                    "🚫 5. 제한 대상\n"  # Reverted to Korean
                    "・다른 클랜과 겹치는 활동 중인 유저\n"  # Reverted to Korean
                    "・트롤, 욕설, 밴 이력 등 제재 기록 있는 유저\n"  # Reverted to Korean
                    "・대리/부계정/계정 공유 등 비정상 활동\n"  # Reverted to Korean
                    "━━━━━━━━━━━━━━━━━━━━━"
                ),
                inline=False
            )
            rules_embed.add_field(
                name="📋 가입 절차",  # Reverted to Korean
                value=(
                    "1️⃣ 디스코드 서버 입장\n"  # Reverted to Korean
                    "2️⃣ 가입 지원서 작성 or 인터뷰\n"  # Reverted to Korean
                    "3️⃣ 트라이아웃 or 최근 경기 클립 확인\n"  # Reverted to Korean
                    "4️⃣ 운영진 승인 → 역할 부여 후 가입 완료"  # Reverted to Korean
                ),
                inline=False
            )
            rules_embed.add_field(
                name="🧠 FAQ",  # Reverted to Korean
                value=(
                    "Q. 마이크 없으면 가입 안 되나요?\n"  # Reverted to Korean
                    "→ 네. 음성 소통은 필수입니다. 텍스트만으로는 활동이 어렵습니다.\n\n"  # Reverted to Korean
                    "Q. 골드 미만인데 들어갈 수 있나요?\n"  # Reverted to Korean
                    "→ 트라이아웃으로 팀워크/이해도 확인 후 예외 승인될 수 있습니다."  # Reverted to Korean
                ),
                inline=False
            )
            rules_embed.set_footer(
                text="✅ 가입 후 일정 기간 적응 평가 기간이 있으며\n"  # Reverted to Korean
                     "매너, 참여도 부족 시 경고 없이 탈퇴될 수 있습니다.\n\n"  # Reverted to Korean
                     "📌 본 안내는 클랜 운영 상황에 따라 변경될 수 있습니다."  # Reverted to Korean
            )

            await channel.send(embed=rules_embed)

            interview_embed = discord.Embed(
                title="✨ 인터뷰 요청 안내 ✨",  # Reverted to Korean
                description=(
                    "Exceed 클랜에 지원하고 싶으신가요?\n"  # Reverted to Korean
                    "아래 버튼을 눌러 인터뷰 요청을 시작하세요.\n"  # Reverted to Korean
                    "신속하게 확인 후 연락드리겠습니다."  # Reverted to Korean
                ),
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            interview_embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/1041/1041916.png")
            interview_embed.set_footer(text="Exceed • 인터뷰 시스템")  # Reverted to Korean
            interview_embed.set_author(
                name="Exceed 인터뷰 안내",  # Reverted to Korean
                icon_url="https://cdn-icons-png.flaticon.com/512/295/295128.png"
            )

            await channel.send(embed=interview_embed, view=InterviewView(self.private_channel_id, self))
            self.logger.info("📨・지원서-제출 채널에 가입 조건 안내 및 인터뷰 버튼을 게시했습니다.")  # Reverted to Korean

        except Exception as e:
            self.logger.error(f"인터뷰 요청 메시지 전송 실패: {e}\n{traceback.format_exc()}")  # Reverted to Korean

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(InterviewView(self.private_channel_id, self))
        self.bot.add_view(DecisionButtonView(cog=self))
        await self.send_interview_request_message()
        self.logger.info("인터뷰 요청 메시지 및 영구 뷰 설정 완료.")  # Reverted to Korean

    @discord.app_commands.command(
        name="request_interview",
        description="인터뷰 요청 메시지를 다시 보냅니다 (관리자용)"  # Reverted to Korean
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def slash_request_interview(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.send_interview_request_message()
        await interaction.followup.send(
            "인터뷰 요청 메시지를 갱신했습니다!",  # Reverted to Korean
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(InterviewRequestCog(bot))