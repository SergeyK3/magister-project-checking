import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/documents.readonly"
]

BASE_DIR = r"D:\MyActivity\MyInfoBusiness\MyPythonApps\12 MagisterProjectChecking"
TOKEN_PATH = os.path.join(BASE_DIR, "credentials", "token.json")


def get_credentials():
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return creds


def extract_text(document):
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


def main():
    DOC_ID = "1fkikkPc7qWAW9tj4UkVvcLwpf412QsaXTKPUW_sQhPQ"

    creds = get_credentials()
    docs_service = build("docs", "v1", credentials=creds)

    document = docs_service.documents().get(documentId=DOC_ID).execute()

    text = extract_text(document)

    print("\nПервые 2000 символов:\n")
    print(text[:2000])


if __name__ == "__main__":
    main()