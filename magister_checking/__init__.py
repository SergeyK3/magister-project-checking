"""Magister project checking: Google Drive / Docs / Sheets automation."""

from magister_checking.docs_extract import HyperlinkRecord, extract_plain_text, iter_hyperlinks

__all__ = [
    "HyperlinkRecord",
    "extract_plain_text",
    "iter_hyperlinks",
    "__version__",
]
__version__ = "0.1.0"
