# utils/gspread_utils.py
import gspread
from google.oauth2.service_account import Credentials
from utils.logger import get_logger
from datetime import datetime, timezone
import os # Added for path checking
import traceback # Added for detailed error logging


class GSpreadClient:
    def __init__(self, credentials_path, members_sheet_name, test_sheet_name):
        self.logger = get_logger("gspread_client")
        self.client = None
        self.members_sheet = None
        self.test_sheet = None

        try:
            if not os.path.exists(credentials_path):
                self.logger.error(f"Google Sheets 자격 증명 파일이 없습니다: {credentials_path}")
                return

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
            self.client = gspread.authorize(creds)

            self.members_sheet = self.client.open(members_sheet_name).sheet1
            self.logger.info(f"Google Sheet: '{members_sheet_name}' 열기 성공.")

            self.test_sheet = self.client.open(test_sheet_name).sheet1
            self.logger.info(f"Google Sheet: '{test_sheet_name}' 열기 성공.")

            self.logger.info("Google Sheets 클라이언트 초기화 성공.")

        except gspread.exceptions.SpreadsheetNotFound as e:
            self.logger.error(f"시트를 찾을 수 없습니다. '{members_sheet_name}' 또는 '{test_sheet_name}' 시트가 공유되었는지 확인하세요. 오류: {e}")
            self.client = None
        except Exception as e:
            self.logger.error(f"Google Sheets 초기화 중 오류 발생: {e}\n{traceback.format_exc()}")
            self.client = None

    def _find_user_row(self, sheet, user_id):
        """Finds a user's row in a sheet based on their Discord User ID (assuming it's in the first column)."""
        if not sheet:
            self.logger.warning(f"시트가 초기화되지 않아 유저를 찾을 수 없습니다. (시트: {sheet.title if sheet else 'None'})")
            return None
        try:
            # Search in the first column for the user ID
            # gspread.findall returns a list of Cell objects
            cells = sheet.findall(str(user_id), in_column=1)
            return cells[0].row if cells else None
        except Exception as e:
            self.logger.error(f"시트 '{sheet.title}'에서 유저 ({user_id})를 찾는 중 오류 발생: {e}\n{traceback.format_exc()}")
            return None

    def add_to_test_sheet(self, user_id, user_name, answers):
        if not self.client or not self.test_sheet:
            self.logger.warning("Google Sheets 클라이언트 또는 테스트 시트가 초기화되지 않아 데이터를 추가할 수 없습니다.")
            return
        self.logger.info(f"{user_name} ({user_id})님을 테스트 시트에 추가합니다.")

        try:
            # Prevent duplicates
            if self._find_user_row(self.test_sheet, user_id):
                self.logger.warning(f"{user_name}님은 이미 테스트 시트에 존재합니다. 업데이트를 시도합니다.")
                # If exists, update the row instead of appending
                row_index = self._find_user_row(self.test_sheet, user_id)
                if row_index:
                    # This list now matches the order of columns in your image
                    row_data = [
                        str(user_id),  # A: 디스코드 id
                        user_name,  # B: 디스코드 이름
                        answers.get("활동 지역 (서부/중부/동부)", ""),  # C: 활동 지역
                        answers.get("인게임 이름 및 태그 (예: 이름#태그)", ""),  # D: 인게임 이름 및 태그
                        answers.get("가장 자신있는 역할", ""),  # E: 가장 자신있는 역할
                        answers.get("프리미어 팀 참가 의향", ""),  # F: 프리미어 팀 참가 의향
                        answers.get("지원 동기", ""),  # G: 지원 동기
                        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # H: 테스트 받은 날짜
                    ]
                    self.test_sheet.update(f'A{row_index}', [row_data])
                    self.logger.info(f"{user_name}님의 테스트 시트 정보를 업데이트했습니다.")
                return

            # This list now matches the order of columns in your image
            row_data = [
                str(user_id),  # A: 디스코드 id
                user_name,  # B: 디스코드 이름
                answers.get("활동 지역 (서부/중부/동부)", ""),  # C: 활동 지역
                answers.get("인게임 이름 및 태그 (예: 이름#태그)", ""),  # D: 인게임 이름 및 태그
                answers.get("가장 자신있는 역할", ""),  # E: 가장 자신있는 역할
                answers.get("프리미어 팀 참가 의향", ""),  # F: 프리미어 팀 참가 의향
                answers.get("지원 동기", ""),  # G: 지원 동기
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # H: 테스트 받은 날짜
            ]
            self.test_sheet.append_row(row_data)
            self.logger.info(f"{user_name}님을 테스트 시트에 성공적으로 추가했습니다.")
        except Exception as e:
            self.logger.error(f"테스트 시트에 {user_name}님을 추가/업데이트 중 오류 발생: {e}\n{traceback.format_exc()}")


    def remove_from_test_sheet(self, user_id, user_name):
        if not self.client or not self.test_sheet:
            self.logger.warning("Google Sheets 클라이언트 또는 테스트 시트가 초기화되지 않아 데이터를 제거할 수 없습니다.")
            return
        self.logger.info(f"{user_name} ({user_id})님을 테스트 시트에서 제거합니다.")

        try:
            row_index = self._find_user_row(self.test_sheet, user_id)
            if row_index:
                self.test_sheet.delete_rows(row_index)
                self.logger.info(f"{user_name}님을 테스트 시트에서 성공적으로 제거했습니다.")
            else:
                self.logger.info(f"{user_name}님은 테스트 시트에 존재하지 않아 제거를 건너뜁니다.")
        except Exception as e:
            self.logger.error(f"테스트 시트에서 {user_name}님을 제거 중 오류 발생: {e}\n{traceback.format_exc()}")


    def add_to_members_sheet(self, user_id, user_name, answers):
        if not self.client or not self.members_sheet:
            self.logger.warning("Google Sheets 클라이언트 또는 멤버 시트가 초기화되지 않아 데이터를 추가할 수 없습니다.")
            return
        self.logger.info(f"{user_name} ({user_id})님을 클랜 멤버 시트에 추가합니다.")

        try:
            # Prevent duplicates
            if self._find_user_row(self.members_sheet, user_id):
                self.logger.warning(f"{user_name}님은 이미 멤버 시트에 존재합니다. 업데이트를 시도합니다.")
                row_index = self._find_user_row(self.members_sheet, user_id)
                if row_index:
                    # This list now matches the order of columns in your image
                    row_data = [
                        str(user_id),
                        user_name,
                        answers.get("인게임 이름 및 태그 (예: 이름#태그)", ""),
                        answers.get("가장 자신있는 역할", ""),
                        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # 가입일
                    ]
                    self.members_sheet.update(f'A{row_index}', [row_data])
                    self.logger.info(f"{user_name}님의 멤버 시트 정보를 업데이트했습니다.")
                return

            row_data = [
                str(user_id),
                user_name,
                answers.get("인게임 이름 및 태그 (예: 이름#태그)", ""),
                answers.get("가장 자신있는 역할", ""),
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # 가입일
            ]
            self.members_sheet.append_row(row_data)
            self.logger.info(f"{user_name}님을 클랜 멤버 시트에 성공적으로 추가했습니다.")
        except Exception as e:
            self.logger.error(f"클랜 멤버 시트에 {user_name}님을 추가/업데이트 중 오류 발생: {e}\n{traceback.format_exc()}")