# utils/gspread_utils.py

import gspread
import asyncio
import os
import traceback
from google.oauth2.service_account import Credentials

class GSpreadClient:
    def __init__(self, credentials_path: str, logger):
        self.credentials_path = credentials_path
        self.logger = logger
        self.gc = None

    async def authorize(self):
        try:
            if not os.path.exists(self.credentials_path):
                self.logger.error(f"❌ Google Sheets credentials file not found at: {self.credentials_path}")
                return False

            self.gc = await asyncio.to_thread(
                gspread.service_account,
                filename=self.credentials_path,
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"
                ]
            )
            self.logger.info("Google Sheets 클라이언트가 성공적으로 인증되었습니다.")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to authorize Google Sheets: {e}\n{traceback.format_exc()}")
            self.gc = None
            return False

    async def get_worksheet(self, spreadsheet_name: str, worksheet_name: str):
        if not self.gc:
            self.logger.error("❌ Google Sheets client not authorized.")
            return None
        try:
            spreadsheet = await asyncio.to_thread(self.gc.open, spreadsheet_name)
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
            all_values = await asyncio.to_thread(worksheet.get_all_values)
            if not all_values:
                self.logger.warning(f"🟡 Worksheet '{worksheet_name}' is empty. Cannot update row.")
                return False

            header = all_values[0]

            interview_id_col_index = -1
            target_col_index = -1

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
            for i, row in enumerate(all_values[1:]):
                if len(row) > interview_id_col_index and row[interview_id_col_index] == interview_id:
                    row_index_to_update = i + 2
                    break

            if row_index_to_update == -1:
                self.logger.warning(f"🟡 Interview ID '{interview_id}' not found in worksheet '{worksheet_name}'. Cannot update status.")
                return False

            await asyncio.to_thread(worksheet.update_cell, row_index_to_update, target_col_index + 1, new_value)
            self.logger.info(f"✅ Updated '{column_to_update}' for interview '{interview_id}' in '{worksheet_name}' to '{new_value}'.")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error updating row for interview '{interview_id}' in '{worksheet_name}': {e}\n{traceback.format_exc()}")
            return False

    # --- Start of new method for deleting rows ---
    async def delete_row_by_interview_id(self, spreadsheet_name: str, worksheet_name: str, interview_id: str):
        worksheet = await self.get_worksheet(spreadsheet_name, worksheet_name)
        if not worksheet:
            return False

        try:
            all_values = await asyncio.to_thread(worksheet.get_all_values)
            if not all_values:
                self.logger.warning(f"🟡 Worksheet '{worksheet_name}' is empty. No rows to delete.")
                return False

            header = all_values[0]
            interview_id_col_index = -1

            for i, col_name in enumerate(header):
                if col_name.strip().lower() == "interview_id":
                    interview_id_col_index = i
                    break

            if interview_id_col_index == -1:
                self.logger.error(f"❌ Column 'Interview_ID' not found in worksheet '{worksheet_name}'. Cannot delete row.")
                return False

            row_index_to_delete = -1
            for i, row in enumerate(all_values[1:]): # Start from 1 to skip header
                if len(row) > interview_id_col_index and row[interview_id_col_index] == interview_id:
                    row_index_to_delete = i + 2 # +2 because we skipped header (0) and Python list is 0-indexed while gspread is 1-indexed for rows
                    break

            if row_index_to_delete == -1:
                self.logger.warning(f"🟡 Interview ID '{interview_id}' not found in worksheet '{worksheet_name}'. No row to delete.")
                return False

            await asyncio.to_thread(worksheet.delete_rows, row_index_to_delete)
            self.logger.info(f"✅ Deleted row for interview '{interview_id}' from '{worksheet_name}' in '{spreadsheet_name}'.")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting row for interview '{interview_id}' in '{worksheet_name}': {e}\n{traceback.format_exc()}")
            return False
    # --- End of new method for deleting rows ---