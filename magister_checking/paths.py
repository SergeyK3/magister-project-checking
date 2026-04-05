"""Project root and credentials paths."""

from pathlib import Path

# magister_checking/ -> repo root
ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = ROOT / "credentials"
CLIENT_SECRET_FILES = (
    CREDENTIALS_DIR / "client_secret.json",
)
TOKEN_PATH = CREDENTIALS_DIR / "token.json"


def resolve_client_secrets_path() -> Path:
    """Return path to OAuth client JSON (Desktop). Prefer client_secret.json."""
    for p in CLIENT_SECRET_FILES:
        if p.is_file():
            return p
    matches = sorted(CREDENTIALS_DIR.glob("client_secret*.json"))
    if matches:
        return matches[0]
    raise FileNotFoundError(
        f"Не найден JSON клиента OAuth. Положите client_secret.json в {CREDENTIALS_DIR} "
        "(скачайте из Google Cloud Console → Credentials → Desktop)."
    )
