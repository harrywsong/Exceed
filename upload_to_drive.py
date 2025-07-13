import os

SCOPES = ['https://www.googleapis.com/auth/drive.file']
SERVICE_ACCOUNT_FILE = 'exceed-465801-9a237edcd3b1.json'
FOLDER_ID = '1QL24lQBS-rtJTieNrgoltTPTukD8XxaL'

def upload_log_to_drive(file_path):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2 import service_account

    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    SERVICE_ACCOUNT_FILE = 'exceed-465801-9a237edcd3b1.json'
    FOLDER_ID = '1QL24lQBS-rtJTieNrgoltTPTukD8XxaL'

    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)

        if not os.path.exists(file_path):
            print(f"❌ Log file {file_path} does not exist.")
            return

        file_name = os.path.basename(file_path)
        file_metadata = {
            'name': file_name,
            'parents': [FOLDER_ID]
        }

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
