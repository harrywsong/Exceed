import os
import pickle
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/drive.file']
TOKEN_PICKLE = 'token.pickle'
FOLDER_ID = "1QL24lQBS-rtJTieNrgoltTPTukD8XxaL"  # Your Google Drive folder ID

def upload_log_to_drive(file_path):
    try:
        if not os.path.exists(file_path):
            print(f"❌ Log file not found: {file_path}")
            return None

        # Load credentials
        creds = None
        if os.path.exists(TOKEN_PICKLE):
            with open(TOKEN_PICKLE, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception("Invalid or missing credentials. Run get_token.py again.")

        # Build Drive API service
        service = build('drive', 'v3', credentials=creds)

        original_name = os.path.basename(file_path)
        name_part, ext_part = os.path.splitext(original_name)

        # Timestamp for upload time
        upload_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Compose filename with timestamp
        drive_filename = f"{name_part}_{upload_timestamp}"

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

        # Delete the local log file after successful upload
        try:
            os.remove(file_path)
            print(f"🗑️ Deleted local log file: {file_path}")
        except Exception as e:
            print(f"⚠️ Failed to delete local log file: {e}")

        return file_id

    except Exception as e:
        print(f"❌ Failed to upload log to Google Drive: {e}")
        return None
