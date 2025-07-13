import os
import pickle
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
import sys  # Import sys for printing to stderr

SCOPES = ['https://www.googleapis.com/auth/drive.file']
TOKEN_PICKLE = 'token.pickle'
FOLDER_ID = "1QL24lQBS-rtJTieNrgoltTPTukD8XxaL"


def upload_file(local_file_path: str, drive_file_name: str) -> str | None:
    """
    Uploads a specified local file to Google Drive with a given name.
    Args:
        local_file_path (str): The full path to the file on the local system.
        drive_file_name (str): The desired name for the file in Google Drive.
    Returns:
        str | None: The ID of the uploaded file if successful, otherwise None.
    """
    try:
        # 1. Check if the file exists locally
        if not os.path.exists(local_file_path):
            print(f"❌ 파일이 로컬에 없습니다: {local_file_path}", file=sys.stderr)
            return None

        # 2. Load or refresh credentials
        creds = None
        if os.path.exists(TOKEN_PICKLE):
            with open(TOKEN_PICKLE, 'rb') as token_file:
                creds = pickle.load(token_file)

        # If credentials are not valid or have expired, try to refresh them
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    print("✅ Google Drive API 자격 증명 새로 고침 성공.", file=sys.stdout)
                except Exception as refresh_error:
                    print(f"❌ Google Drive API 자격 증명 새로 고침 실패: {refresh_error}", file=sys.stderr)
                    print("Google Drive 업로드를 위해 'token.pickle'을 다시 생성해야 할 수 있습니다.", file=sys.stderr)
                    return None
            else:
                # If no refresh token or initial creds are missing/invalid, indicate manual auth is needed
                print("❌ 유효하거나 누락된 Google Drive API 자격 증명. 인증 흐름을 다시 실행해야 합니다.", file=sys.stderr)
                return None

        # 3. Build the Google Drive service
        service = build('drive', 'v3', credentials=creds)

        file_metadata = {
            'name': drive_file_name,
            'parents': [FOLDER_ID]
        }
        media = MediaFileUpload(local_file_path, mimetype='text/plain')

        # 5. Execute the upload
        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'  # Request only the ID of the uploaded file
        ).execute()

        file_id = uploaded_file.get('id')
        print(f"✅ Google Drive에 {local_file_path}를 {drive_file_name}으로 업로드했습니다.", file=sys.stdout)
        print(f"🔗 파일 링크: https://drive.google.com/file/d/{file_id}/view", file=sys.stdout)

        # 6. Delete the local file after successful upload
        try:
            os.remove(local_file_path)
            print(f"🗑️ 로컬 파일 삭제됨: {local_file_path}", file=sys.stdout)
        except Exception as delete_error:
            print(f"⚠️ 로컬 파일 삭제 실패: {delete_error}", file=sys.stderr)

        return file_id

    except Exception as e:
        print(f"❌ Google Drive에 파일 업로드 실패: {e}", file=sys.stderr)
        return None

