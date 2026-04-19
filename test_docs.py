import os
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

BASE_DIR = r"D:\MyActivity\MyInfoBusiness\MyPythonApps\12 MagisterProjectChecking"
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials", "client_secret.json")
TOKEN_PATH = os.path.join(BASE_DIR, "credentials", "token.json")


def get_credentials() -> Credentials:
    creds: Optional[Credentials] = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        return creds

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


def extract_text_from_doc(document: dict) -> str:
    parts = []

    for element in document.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue

        for pe in paragraph.get("elements", []):
            text_run = pe.get("textRun")
            if text_run and "content" in text_run:
                parts.append(text_run["content"])

    return "".join(parts)


def main() -> None:
    creds = get_credentials()

    drive_service = build("drive", "v3", credentials=creds)
    docs_service = build("docs", "v1", credentials=creds)

    results = drive_service.files().list(
        pageSize=20,
        fields="files(id, name, mimeType)"
    ).execute()

    items = results.get("files", [])

    print("Файлы на Google Drive:")
    for item in items:
        print(f"- {item['name']} | {item['mimeType']} | {item['id']}")

    doc_item = next(
        (x for x in items if x["mimeType"] == "application/vnd.google-apps.document"),
        None
    )

    if not doc_item:
        print("\nGoogle Docs документ среди первых 20 файлов не найден.")
        return

    print("\nБерём первый найденный Google Docs документ:")
    print(f"{doc_item['name']} ({doc_item['id']})")

    document = docs_service.documents().get(documentId=doc_item["id"]).execute()
    text = extract_text_from_doc(document)

    print("\nПервые 3000 символов документа:\n")
    print(text[:3000])


if __name__ == "__main__":
    main()