import os
import pickle
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/drive.file']
TOKEN_PICKLE = 'token.pickle'
FOLDER_ID = "1QL24lQBS-rtJTieNrgoltTPTukD8XxaL"  # Your Google Drive folder ID

def upload_log_to_drive(file_path: str) -> str | None:
    """
    Uploads a log file to Google Drive in the specified folder, appending a timestamp
    to the filename on Drive. Deletes the local file upon successful upload.

    Args:
        file_path (str): Path to the local log file to upload.

    Returns:
        str | None: The Google Drive file ID of the uploaded file, or None if upload failed.
    """
    try:
        if not os.path.exists(file_path):
            print(f"❌ Log file not found: {file_path}")
            return None

        # Load stored credentials from token.pickle
        creds = None
        if os.path.exists(TOKEN_PICKLE):
            with open(TOKEN_PICKLE, 'rb') as token_file:
                creds = pickle.load(token_file)

        # Refresh or raise if creds invalid
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception("Invalid or missing credentials. Please run the auth flow again.")

        # Build the Drive API service client
        service = build('drive', 'v3', credentials=creds)

        # Get extension of the local file (e.g., ".log")
        _, ext = os.path.splitext(file_path)

        # Create timestamped filename for Google Drive
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        drive_filename = f"{timestamp}{ext}"

        file_metadata = {
            'name': drive_filename,
            'parents': [FOLDER_ID]
        }
        media = MediaFileUpload(file_path, mimetype='text/plain')

        # Upload file
        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        file_id = uploaded_file.get('id')
        print(f"✅ Uploaded {file_path} to Google Drive as {drive_filename}")
        print(f"🔗 File link: https://drive.google.com/file/d/{file_id}/view")

        # Delete local log file after successful upload
        try:
            os.remove(file_path)
            print(f"🗑️ Deleted local log file: {file_path}")
        except Exception as delete_error:
            print(f"⚠️ Failed to delete local log file: {delete_error}")

        return file_id

    except Exception as e:
        print(f"❌ Failed to upload log to Google Drive: {e}")
        return None
