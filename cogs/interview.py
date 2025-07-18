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

            await member.add_roles(test_role, reason="Test role granted (Admin approval)")
            self.cog.logger.info(f"🟡 Granted test role '{test_role.name}' to {member.display_name} ({member.id}).")

            try:
                await member.send(
                    "Hello.\n\n"
                    "Thank you for your interest in the Exceed clan.\n"
                    "To further assess your potential and enthusiasm, we have granted you the **Test Role**.\n\n"
                    "With this role, you are free to be active on the server during the test period,\n"
                    "and the operations team will make a final decision based on your activity and communication.\n\n"
                    "Exceed values teamwork and community atmosphere, so we expect active participation and positive communication during the test period.\n\n"
                    "If you have any questions or inconveniences, please feel free to contact the operations team at any time.\n"
                    "You can also use the channel below for inquiries:\n\n"
                    "https://discord.com/channels/1389527318699053178/1389742771253805077\n\n"
                    "Thank you again for applying, and we look forward to your future activities!\n\n"
                    "Thank you.\n\n"
                    "📌 *This message was automatically sent, and replies directly to this bot will not be seen by the operations team.*"
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
                    "Hello. \n\n"
                    "First, thank you for your interest and application to the Exceed clan.\n"
                    "Unfortunately, for various reasons, we cannot proceed with your application at this time.\n"
                    "We sincerely appreciate your passion and effort, but please understand that this decision was made after comprehensive consideration of the current clan situation and various factors.\n"
                    "We sincerely hope for your continued growth and encourage you to reapply whenever circumstances allow. \n\n"
                    "Exceed is always open, and we look forward to the opportunity to have you join us in the future.\n\n"
                    "If you have any questions, please feel free to contact the operations team or reach out through the channel below:  \n\n"
                    "https://discord.com/channels/1389527318699053178/1389742771253805077\n\n"
                    "Thank you.\n\n"
                    "📌 *This message was automatically sent, and replies directly to this bot will not be seen by the operations team.*"
                )
                self.cog.logger.info(f"❌ Sent rejection DM to {member.display_name}.")
            except discord.Forbidden:
                self.cog.logger.warning(
                    f"❌ Could not send DM to {member.display_name} ({member.id}). (DM disabled or blocked)")
                await interaction.followup.send(f"❌ Rejected {member.mention}. (DM failed: DM might be disabled.)")
                return

            applicant_role = interaction.guild.get_role(APPLICANT_ROLE_ID)
            if applicant_role and applicant_role in member.roles:
                await member.remove_roles(applicant_role, reason="Applicant role removed due to rejection")
                self.cog.logger.info(f"Removed applicant role '{applicant_role.name}' from {member.display_name}.")

            await interaction.followup.send(f"❌ Rejected {member.mention}.")
            self.cog.logger.info(f"❌ Rejected {member.display_name} ({member.id}).")

        except Exception as e:
            self.cog.logger.error(f"❌ Error during rejection process: {e}\n{traceback.format_exc()}")
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)


class InterviewModal(Modal, title="Interview Pre-Questions"):
    def __init__(self):
        super().__init__()
        self.answers = {}

        self.add_item(TextInput(
            label="Activity Region (West/Central/East)",
            placeholder="e.g., Central",
            style=TextStyle.short,
            required=True,
            max_length=20
        ))
        self.add_item(TextInput(
            label="In-game Name and Tag (e.g., Name#Tag)",
            placeholder="e.g., RiotName#RiotTag",
            style=TextStyle.short,
            required=True,
            max_length=50
        ))
        self.add_item(TextInput(
            label="Most Confident Role",
            placeholder="e.g., Duelist, Sentinel, Initiator, etc.",
            style=TextStyle.short,
            required=True,
            max_length=30
        ))
        self.add_item(TextInput(
            label="Intention to Join Premier Team",
            placeholder="e.g., Yes / No",
            style=TextStyle.short,
            required=True,
            max_length=10
        ))
        self.add_item(TextInput(
            label="Reason for Application",
            placeholder="Briefly state your reason for applying to Exceed.",
            style=TextStyle.paragraph,
            required=True,
            max_length=300
        ))

    async def on_submit(self, interaction: discord.Interaction):
        for item in self.children:
            self.answers[item.label] = item.value.strip()

        region = self.answers.get("Activity Region (West/Central/East)", "")
        if region not in ("West", "Central", "East"):  # Updated to English regions for consistency
            return await interaction.response.send_message(
                "❌ Please enter a valid activity region (West, Central, or East).",
                ephemeral=True
            )

        cog = interaction.client.get_cog("InterviewRequestCog")
        if not cog:
            fallback_logger = get_logger("interview_modal_fallback")
            fallback_logger.error("❌ Interview cog not found in on_submit.")
            return await interaction.response.send_message(
                "❌ Interview cog not found.",
                ephemeral=True
            )

        private_channel = interaction.guild.get_channel(cog.private_channel_id)
        if not private_channel:
            cog.logger.error(f"❌ Private channel not found. ID: {cog.private_channel_id}")
            return await interaction.response.send_message(
                "❌ Private channel not found.",
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
                self.answers.get("Activity Region (West/Central/East)", ""),
                self.answers.get("In-game Name and Tag (e.g., Name#Tag)", ""),
                self.answers.get("Most Confident Role", ""),
                self.answers.get("Intention to Join Premier Team", ""),
                self.answers.get("Reason for Application", ""),
                "Submitted"  # Initial Status after submission (Changed to English)
            ]
            cog.logger.info(f"Attempting to append row to 'Testing' sheet for user: {interaction.user.id}")
            success = await cog.gspread_client.append_row(
                config.GSHEET_TESTING_SPREADSHEET_NAME, "Sheet1", data_row
            )
            if not success:
                cog.logger.error(
                    f"❌ Failed to add data to 'Testing' sheet from InterviewModal for user: {interaction.user.id}. GSpreadClient returned False.")
                return await interaction.response.send_message(
                    "❌ Failed to save interview information to Google Sheet. Please try again.",
                    ephemeral=True
                )
            cog.logger.info(f"✅ Interview data successfully saved to 'Testing' sheet: {interaction.user.id}")

        except Exception as e:
            cog.logger.error(
                f"❌ Error while saving Google Sheet data from InterviewModal: {e}\n{traceback.format_exc()}")
            return await interaction.response.send_message(
                f"❌ An error occurred while processing interview data: {str(e)}",
                ephemeral=True
            )

        embed = discord.Embed(
            title="📝 Interview Request Received",
            description=f"{interaction.user.mention} has requested an interview.",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_author(name="Exceed Interview System")

        for question, answer in self.answers.items():
            embed.add_field(
                name=f"❓ {question}",
                value=f"> {answer or '*No response*'}",
                inline=False
            )

        view = DecisionButtonView(applicant_id=interaction.user.id, cog=cog)
        await private_channel.send(embed=embed, view=view)
        cog.logger.info(f"Interview request received: {interaction.user.display_name} ({interaction.user.id})")

        await interaction.response.send_message(
            "✅ Your interview request has been successfully sent!",
            ephemeral=True
        )


class InterviewView(View):
    def __init__(self, private_channel_id: int, cog):
        super().__init__(timeout=None)
        self.private_channel_id = private_channel_id
        self.cog = cog

    @discord.ui.button(label="Start Interview Request", style=discord.ButtonStyle.primary, custom_id="start_interview")
    async def start_interview(self, interaction: discord.Interaction, button: Button):
        modal = InterviewModal()
        await interaction.response.send_modal(modal)


class InterviewRequestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.public_channel_id = INTERVIEW_PUBLIC_CHANNEL_ID
        self.private_channel_id = INTERVIEW_PRIVATE_CHANNEL_ID

        self.logger = logger_module.get_logger(self.__class__.__name__)
        self.logger.info("InterviewRequestCog initialized.")
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

        text = f"Congratulations, {member.display_name}!"

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
            self.logger.error(f"Welcome channel ID {WELCOME_CHANNEL_ID} not found.")
            return

        file = None
        try:
            card_buf = await self.make_congrats_card(member)
            if card_buf:
                file = File(card_buf, filename="welcome.png")
            else:
                self.logger.warning(
                    f"Failed to create welcome card for {member.display_name}. Sending message without file.")

            embed = discord.Embed(
                title=f"🎉 Congratulations, {member.display_name}! You've joined Exceed Clan!",
                description="Congratulations! We sincerely welcome you as an official clan member.",
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="1️⃣ Please be sure to check the clan rules!", value=f"<#{config.RULES_CHANNEL_ID}>",
                            inline=False)
            embed.add_field(name="2️⃣ Select your desired role in the role assignment channel.",
                            value=f"<#{config.ROLE_ASSIGN_CHANNEL_ID}>",
                            inline=False)
            embed.add_field(name="3️⃣ Check out the member-only chat room.",
                            value=f"<#{config.MEMBER_CHAT_CHANNEL_ID}>", inline=False)
            embed.add_field(name="4️⃣ Check the clan leaderboard based on the clan MMR system.",
                            value=f"<#{config.CLAN_LEADERBOARD_CHANNEL_ID}>", inline=False)

            if file:
                embed.set_image(url="attachment://welcome.png")

            embed.set_footer(text="Exceed • Welcome Message", icon_url=self.bot.user.display_avatar.url)

            await channel.send(
                content=member.mention,
                embed=embed,
                file=file,
                allowed_mentions=discord.AllowedMentions(users=True))
            self.logger.info(f"Welcome message sent: {member.display_name} ({member.id})")

        except Exception as e:
            self.logger.error(f"Failed to send welcome message: {str(e)}\n{traceback.format_exc()}")

    async def send_interview_request_message(self):
        channel = self.bot.get_channel(self.public_channel_id)
        if not channel:
            self.logger.error(f"Public channel ID {self.public_channel_id} not found.")
            return

        try:
            await channel.purge(limit=None)
            self.logger.info(f"Purged existing messages in channel #{channel.name} ({channel.id}).")

            rules_embed = discord.Embed(
                title="🎯 XCD Valorant Clan Membership Requirements",
                description="📜 Last Updated: 2025.07.06",
                color=discord.Color.orange()
            )
            rules_embed.add_field(
                name="Please confirm the conditions below before joining.",
                value=(
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "🔞 1. Age Requirement\n"
                    "・20 years old or older (born before 2005)\n"
                    "・Expect mature communication and responsible behavior.\n\n"
                    "🎮 2. Skill Requirement\n"
                    "・Current rank Gold or higher (Episode based)\n"
                    "・Skill can be verified through tryouts (scrim tests)\n"
                    "・Game understanding & teamwork will also be evaluated\n\n"
                    "💬 3. Manners & Communication\n"
                    "・No abusive language/disrespect/mocking/informal speech\n"
                    "・Able to accept feedback and play with a positive attitude\n"
                    "・Discord microphone required\n\n"
                    "⏱️ 4. Activity\n"
                    "・Able to connect & play at least 3 times a week\n"
                    "・Willingness to actively participate in tournaments/scrims/internal matches etc.\n"
                    "・Automatic removal after 30 days of inactivity\n\n"
                    "🚫 5. Restricted Individuals\n"
                    "・Users active in other clans simultaneously\n"
                    "・Users with records of trolling, abusive language, bans, etc.\n"
                    "・Abnormal activities such as boosting/smurfing/account sharing\n"
                    "━━━━━━━━━━━━━━━━━━━━━"
                ),
                inline=False
            )
            rules_embed.add_field(
                name="📋 Joining Procedure",
                value=(
                    "1️⃣ Join Discord Server\n"
                    "2️⃣ Fill out Application Form or Interview\n"
                    "3️⃣ Tryout or Recent Match Clip Review\n"
                    "4️⃣ Operations Team Approval → Role Assignment → Completion of Joining"
                ),
                inline=False
            )
            rules_embed.add_field(
                name="🧠 FAQ",
                value=(
                    "Q. Can I join without a microphone?\n"
                    "→ No. Voice communication is mandatory. It's difficult to be active with text only.\n\n"
                    "Q. Can I join if I'm below Gold rank?\n"
                    "→ Exceptional approval may be granted after evaluating teamwork/understanding through tryouts."
                ),
                inline=False
            )
            rules_embed.set_footer(
                text="✅ After joining, there will be an adjustment period\n"
                     "and you may be removed without warning for lack of manners or participation.\n\n"
                     "📌 This guide may be subject to change based on clan operations."
            )

            await channel.send(embed=rules_embed)

            interview_embed = discord.Embed(
                title="✨ Interview Request Guide ✨",
                description=(
                    "Interested in joining the Exceed clan?\n"
                    "Click the button below to start your interview request.\n"
                    "We will review it promptly and contact you."
                ),
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            interview_embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/1041/1041916.png")
            interview_embed.set_footer(text="Exceed • Interview System")
            interview_embed.set_author(
                name="Exceed Interview Guide",
                icon_url="https://cdn-icons-png.flaticon.com/512/295/295128.png"
            )

            await channel.send(embed=interview_embed, view=InterviewView(self.private_channel_id, self))
            self.logger.info("📨・Application-Submission channel: Posted membership requirements and interview button.")

        except Exception as e:
            self.logger.error(f"Failed to send interview request message: {e}\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(InterviewView(self.private_channel_id, self))
        self.bot.add_view(DecisionButtonView(cog=self))
        await self.send_interview_request_message()
        self.logger.info("Interview request message and persistent views set up.")

    @discord.app_commands.command(
        name="request_interview",
        description="Resends the interview request message (Admin only)"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def slash_request_interview(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.send_interview_request_message()
        await interaction.followup.send(
            "Interview request message refreshed!",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(InterviewRequestCog(bot))