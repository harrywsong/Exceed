# utils/gspread_utils.py
import gspread
import asyncio
import os
import traceback
from google.oauth2.service_account import Credentials # Still needed for get_creds_for_gsheets

class GSpreadClient:
    def __init__(self, credentials_path: str, logger):
        self.credentials_path = credentials_path
        self.logger = logger
        self.gc = None # This will store the gspread client

    async def authorize(self):
        try:
            # Check if credentials file exists
            if not os.path.exists(self.credentials_path):
                self.logger.error(f"❌ Google Sheets credentials file not found at: {self.credentials_path}")
                return False

            # Authenticate using the service account file
            # gspread.service_account() is synchronous, so run in a thread
            self.gc = await asyncio.to_thread(
                gspread.service_account,
                filename=self.credentials_path,
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"
                ]
            )
            self.logger.info("✅ Google Sheets client authorized successfully using gspread.service_account().")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to authorize Google Sheets: {e}\n{traceback.format_exc()}")
            self.gc = None # Ensure client is reset on failure
            return False

    async def get_worksheet(self, spreadsheet_name: str, worksheet_name: str):
        if not self.gc:
            self.logger.error("❌ Google Sheets client not authorized.")
            return None
        try:
            # Open spreadsheet is synchronous
            spreadsheet = await asyncio.to_thread(self.gc.open, spreadsheet_name)
            # Get worksheet is synchronous
            worksheet = await asyncio.to_thread(spreadsheet.worksheet, worksheet_name)
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
                # append_row is synchronous
                await asyncio.to_thread(worksheet.append_row, data)
                self.logger.info(f"✅ Appended row to '{worksheet_name}' in '{spreadsheet_name}': {data}")
                return True
            except Exception as e:
                self.logger.error(f"❌ Error appending row to '{worksheet_name}' in '{spreadsheet_name}': {e}\n{traceback.format_exc()}")
                return False
        return False

    async def update_row_by_interview_id(self, spreadsheet_name: str, worksheet_name: str, interview_id: str, column_to_update: str, new_value: str):
        worksheet = await self.get_worksheet(spreadsheet_name, worksheet_name)
        if not worksheet:
            return False

        try:
            # Get all values is synchronous
            all_values = await asyncio.to_thread(worksheet.get_all_values)
            if not all_values:
                self.logger.warning(f"🟡 Worksheet '{worksheet_name}' is empty. Cannot update row.")
                return False

            header = all_values[0] # First row is the header

            interview_id_col_index = -1
            target_col_index = -1

            # Find column indices (case-insensitive for robustness)
            for i, col_name in enumerate(header):
                if col_name.strip().lower() == "interview_id":
                    interview_id_col_index = i
                if col_name.strip().lower() == column_to_update.lower():
                    target_col_index = i

            if interview_id_col_index == -1:
                self.logger.error(f"❌ Column 'Interview_ID' not found in worksheet '{worksheet_name}'. Cannot update row.")
                return False
            if target_col_index == -1:
                self.logger.error(f"❌ Column '{column_to_update}' not found in worksheet '{worksheet_name}'. Cannot update row.")
                return False

            row_index_to_update = -1
            # Iterate through rows starting from the second row (index 1)
            for i, row in enumerate(all_values[1:]): # Start from 1 to skip header
                if len(row) > interview_id_col_index and row[interview_id_col_index] == interview_id:
                    row_index_to_update = i + 2 # +2 because we skipped header (0) and Python list is 0-indexed while gspread is 1-indexed for rows
                    break

            if row_index_to_update == -1:
                self.logger.warning(f"🟡 Interview ID '{interview_id}' not found in worksheet '{worksheet_name}'. Cannot update status.")
                return False

            # Update the specific cell is synchronous
            await asyncio.to_thread(worksheet.update_cell, row_index_to_update, target_col_index + 1, new_value) # +1 for gspread column index
            self.logger.info(f"✅ Updated '{column_to_update}' for interview '{interview_id}' in '{worksheet_name}' to '{new_value}'.")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error updating row for interview '{interview_id}' in '{worksheet_name}': {e}\n{traceback.format_exc()}")
            return False