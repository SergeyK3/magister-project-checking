import gspread
from google.oauth2.service_account import Credentials

SERVICE_ACCOUNT_FILE = r"d:\MyActivity\MyInfoBusiness\MyPythonApps\12 MagisterProjectChecking\key\magister-checking-dev-f10f50569a94.json"
SPREADSHEET_ID = "16gpZSZgKBcbf8Z9LZvcYKT1lUPG-URuo9C6K9BRPDHU"
WORKSHEET_NAME = "Участники"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=SCOPES
)

client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SPREADSHEET_ID)
worksheet = spreadsheet.worksheet(WORKSHEET_NAME)

worksheet.update("A2:C2", [["ТЕСТ", "Magister Checking Dev", "OK"]])

print("Успешно: таблица доступна, запись выполнена.")