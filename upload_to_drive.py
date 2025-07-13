import os
import pickle
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/drive.file']
TOKEN_PICKLE = 'token.pickle'

def upload_log_to_drive(file_path):
    try:
        creds = None
        if os.path.exists(TOKEN_PICKLE):
            with open(TOKEN_PICKLE, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception("Credentials are invalid or missing. Run get_token.py again.")

        service = build('drive', 'v3', credentials=creds)

        if not os.path.exists(file_path):
            print(f"❌ Log file {file_path} does not exist.")
            return

        file_name = os.path.basename(file_path)
        file_metadata = {'name': file_name}

        media = MediaFileUpload(file_path, mimetype='text/plain')

        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        print(f"✅ Uploaded {file_path} to Google Drive with ID: {uploaded_file.get('id')}")

    except Exception as e:
        print(f"❌ Failed to upload log to Google Drive: {e}")
        raise
