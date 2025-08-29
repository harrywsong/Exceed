import os
import pickle
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/drive.file']
TOKEN_PICKLE = 'token1.pickle'
FOLDER_ID = "1QL24lQBS-rtJTieNrgoltTPTukD8XxaL"

def upload_log_to_drive(file_path: str) -> str | None:
    try:
        if not os.path.exists(file_path):
            print(f"❌ Log file not found: {file_path}")
            return None

        creds = None
        if os.path.exists(TOKEN_PICKLE):
            with open(TOKEN_PICKLE, 'rb') as token_file:
                creds = pickle.load(token_file)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception("Invalid or missing credentials. Please run the auth flow again.")

        service = build('drive', 'v3', credentials=creds)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        drive_filename = f"{timestamp}.log"

        file_metadata = {
            'name': drive_filename,
            'parents': [FOLDER_ID]
        }
        media = MediaFileUpload(file_path, mimetype='text/plain')

        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        file_id = uploaded_file.get('id')
        print(f"✅ Uploaded {file_path} to Google Drive as {drive_filename}")
        print(f"🔗 File link: https://drive.google.com/file/d/{file_id}/view")

        try:
            os.remove(file_path)
            print(f"🗑️ Deleted local log file: {file_path}")
        except Exception as delete_error:
            print(f"⚠️ Failed to delete local log file: {delete_error}")

        return file_id

    except Exception as e:
        print(f"❌ Failed to upload log to Google Drive: {e}")
        return None
