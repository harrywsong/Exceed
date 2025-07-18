# utils/gspread_client.py
import gspread
import gspread_asyncio
import google.auth
from google.oauth2.service_account import Credentials
import asyncio
import os
import traceback

class GSpreadClient:
    def __init__(self, credentials_path: str, logger):
        self.credentials_path = credentials_path
        self.logger = logger
        self.client_manager = None

    async def authorize(self):
        try:
            # Check if credentials file exists
            if not os.path.exists(self.credentials_path):
                self.logger.error(f"❌ Google Sheets credentials file not found at: {self.credentials_path}")
                return False

            gc = gspread_asyncio.AsyncioGSpreadClientManager(
                self.get_creds_for_gsheets
            )
            self.client_manager = gc
            self.logger.info("✅ Google Sheets client manager initialized.")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to authorize Google Sheets: {e}\n{traceback.format_exc()}")
            return False

    def get_creds_for_gsheets(self):
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            creds = Credentials.from_service_account_file(
                self.credentials_path, scopes=scopes
            )
            self.logger.info("✅ Google Sheets credentials loaded.")
            return creds
        except Exception as e:
            self.logger.error(f"❌ Error loading Google Sheets credentials: {e}\n{traceback.format_exc()}")
            raise

    async def get_worksheet(self, spreadsheet_name: str, worksheet_name: str):
        if not self.client_manager:
            self.logger.error("❌ Google Sheets client not authorized.")
            return None
        try:
            gc = await self.client_manager.authorize()
            spreadsheet = await gc.open(spreadsheet_name)
            worksheet = await spreadsheet.worksheet(worksheet_name)
            self.logger.info(f"✅ Successfully accessed worksheet '{worksheet_name}' in spreadsheet '{spreadsheet_name}'.")
            return worksheet
        except gspread.exceptions.SpreadsheetNotFound:
            self.logger.error(f"❌ Spreadsheet '{spreadsheet_name}' not found. Please check the name and sharing permissions.")
            return None
        except gspread.exceptions.WorksheetNotFound:
            self.logger.error(f"❌ Worksheet '{worksheet_name}' not found in spreadsheet '{spreadsheet_name}'. Please check the name.")
            return None
        except Exception as e:
            self.logger.error(f"❌ Error getting worksheet '{worksheet_name}' from '{spreadsheet_name}': {e}\n{traceback.format_exc()}")
            return None

    async def append_row(self, spreadsheet_name: str, worksheet_name: str, data: list):
        worksheet = await self.get_worksheet(spreadsheet_name, worksheet_name)
        if worksheet:
            try:
                await worksheet.append_row(data)
                self.logger.info(f"✅ Appended row to '{worksheet_name}' in '{spreadsheet_name}': {data}")
                return True
            except Exception as e:
                self.logger.error(f"❌ Error appending row to '{worksheet_name}' in '{spreadsheet_name}': {e}\n{traceback.format_exc()}")
                return False
        return False

    async def update_row_by_user_id(self, spreadsheet_name: str, worksheet_name: str, user_id: int, column_to_update: str, new_value: str):
        worksheet = await self.get_worksheet(spreadsheet_name, worksheet_name)
        if not worksheet:
            return False

        try:
            # Get all records to find the row index based on user_id
            all_data = await worksheet.get_all_records()
            header = await worksheet.row_values(1) # Get header row

            user_id_col_index = -1
            status_col_index = -1

            # Find column indices (case-insensitive for robustness)
            for i, col_name in enumerate(header):
                if col_name.strip().lower() == "discord_user_id":
                    user_id_col_index = i + 1 # gspread is 1-indexed
                if col_name.strip().lower() == column_to_update.lower():
                    status_col_index = i + 1 # gspread is 1-indexed

            if user_id_col_index == -1:
                self.logger.error(f"❌ Column 'Discord_User_ID' not found in worksheet '{worksheet_name}'. Cannot update row.")
                return False
            if status_col_index == -1:
                self.logger.error(f"❌ Column '{column_to_update}' not found in worksheet '{worksheet_name}'. Cannot update row.")
                return False

            row_index = -1
            for i, row in enumerate(all_data):
                # Using .get() for safer access in case column is missing in some rows
                if row.get("Discord_User_ID") == user_id:
                    row_index = i + 2 # +2 because gspread.get_all_records() skips header and is 0-indexed internally
                    break

            if row_index == -1:
                self.logger.warning(f"🟡 User ID {user_id} not found in worksheet '{worksheet_name}'. Cannot update status.")
                return False

            await worksheet.update_cell(row_index, status_col_index, new_value)
            self.logger.info(f"✅ Updated status for user {user_id} in '{worksheet_name}' to '{new_value}'.")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error updating row for user {user_id} in '{worksheet_name}': {e}\n{traceback.format_exc()}")
            return False