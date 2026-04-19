from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/documents.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

BASE_DIR = r"D:\MyActivity\MyInfoBusiness\MyPythonApps\12 MagisterProjectChecking"
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials", "client_secret.json")

flow = InstalledAppFlow.from_client_secrets_file(
    CREDENTIALS_PATH, SCOPES
)

creds = flow.run_local_server(port=0)

service = build('drive', 'v3', credentials=creds)

results = service.files().list(pageSize=10).execute()
items = results.get('files', [])

print("Файлы на Google Drive:")
for item in items:
    print(f"{item['name']} ({item['id']})")