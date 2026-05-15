"""Разведка перед фичей подробного compliance: проверяем, что мой парсер
margins/footer на DOCX Камзебаевой (row 2) даёт те же числа, что и
визуальная проверка (1,83 / 2,12 / 1,75 / 0,75 см; нумерация внизу по центру).

Если совпадёт — формула 567 twips/см верна, footer-PAGE детекция работает,
дальше можно реализовывать DissertationMetrics-расширение и .env-правила.
Если нет — лучше узнать сейчас, чем зашить ошибку и переделывать.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from google.oauth2.service_account import Credentials  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaIoBaseDownload  # noqa: E402

from magister_checking.bot.config import load_config  # noqa: E402

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W_NS, "r": R_NS}

DISSERTATION_FILE_ID = "1kwJ4RDPSk4a0_WXaTZIlXSPn-xcvl3do"  # Камзебаева row 2


def _download_bytes(drive, file_id: str) -> bytes:
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    return fh.getvalue()


def _twips_to_cm(twips: str | None) -> float | None:
    if twips is None:
        return None
    try:
        return round(int(twips) / 567, 2)
    except (TypeError, ValueError):
        return None


def _dump_pgmar(zf: zipfile.ZipFile) -> None:
    print("== Поля страницы и привязка footer (по sectPr) ==")
    try:
        doc_xml = zf.read("word/document.xml")
    except KeyError:
        print("  word/document.xml: НЕ найден")
        return
    root = ET.fromstring(doc_xml)
    body = root.find(f"{{{W_NS}}}body")
    if body is None:
        print("  body: НЕ найден")
        return
    sectprs = body.findall(f".//{{{W_NS}}}sectPr")
    print(f"  найдено sectPr: {len(sectprs)}")
    for i, sectpr in enumerate(sectprs):
        pgmar = sectpr.find(f"{{{W_NS}}}pgMar")
        cm: dict[str, float | None] = {}
        if pgmar is not None:
            for key in ("top", "bottom", "left", "right"):
                cm[key] = _twips_to_cm(pgmar.get(f"{{{W_NS}}}{key}"))
        footer_refs = sectpr.findall(f"{{{W_NS}}}footerReference")
        header_refs = sectpr.findall(f"{{{W_NS}}}headerReference")
        title_pg = sectpr.find(f"{{{W_NS}}}titlePg") is not None
        pg_num = sectpr.find(f"{{{W_NS}}}pgNumType")
        pg_num_attrs = (
            {k.split("}", 1)[-1]: v for k, v in pg_num.attrib.items()}
            if pg_num is not None
            else None
        )
        parent = sectpr.getparent() if hasattr(sectpr, "getparent") else None
        loc_hint = "(в w:body, наследуется в неуказанных секциях)"
        if parent is not None and parent.tag.endswith("}pPr"):
            loc_hint = "(внутри w:p — конец секции с разрывом)"
        elif parent is not None and parent.tag.endswith("}body"):
            loc_hint = "(прямой ребёнок w:body — final body sectPr)"

        print(
            f"  sectPr[{i}] pgMar (см)={cm} "
            f"titlePg={title_pg} pgNumType={pg_num_attrs} "
            f"footerRefs={len(footer_refs)} headerRefs={len(header_refs)} "
            f"{loc_hint}"
        )
        for ref in footer_refs:
            print(
                f"    footerReference type={ref.get(f'{{{W_NS}}}type')!r} "
                f"rId={ref.get(f'{{{R_NS}}}id')!r}"
            )
        for ref in header_refs:
            print(
                f"    headerReference type={ref.get(f'{{{W_NS}}}type')!r} "
                f"rId={ref.get(f'{{{R_NS}}}id')!r}"
            )


def _dump_footers(zf: zipfile.ZipFile) -> None:
    print("\n== Footer'ы и нумерация страниц ==")
    footer_names = [n for n in zf.namelist() if n.startswith("word/footer") and n.endswith(".xml")]
    print(f"  footer-файлов в архиве: {len(footer_names)} → {footer_names}")

    try:
        rels_xml = zf.read("word/_rels/document.xml.rels")
        rels_root = ET.fromstring(rels_xml)
    except KeyError:
        rels_root = None
    rel_id_to_target: dict[str, str] = {}
    if rels_root is not None:
        for rel in rels_root.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
            if "footer" in (rel.get("Type") or ""):
                rel_id_to_target[rel.get("Id") or ""] = rel.get("Target") or ""
    print(f"  footer-rels: {rel_id_to_target}")

    try:
        doc_xml = zf.read("word/document.xml")
    except KeyError:
        return
    doc_root = ET.fromstring(doc_xml)
    refs = doc_root.findall(f".//{{{W_NS}}}footerReference")
    print(f"  footerReference в document.xml: {len(refs)}")
    for ref in refs:
        ftype = ref.get(f"{{{W_NS}}}type")
        rid = ref.get(f"{{{R_NS}}}id")
        target = rel_id_to_target.get(rid or "")
        print(f"    type={ftype!r} rId={rid!r} target={target!r}")

    styles_xml = None
    try:
        styles_xml = zf.read("word/styles.xml")
    except KeyError:
        pass
    style_jc: dict[str, str] = {}
    if styles_xml is not None:
        sroot = ET.fromstring(styles_xml)
        for style in sroot.findall(f"{{{W_NS}}}style"):
            sid = style.get(f"{{{W_NS}}}styleId") or ""
            ppr = style.find(f"{{{W_NS}}}pPr")
            if ppr is None:
                continue
            jc_el = ppr.find(f"{{{W_NS}}}jc")
            if jc_el is not None:
                style_jc[sid] = jc_el.get(f"{{{W_NS}}}val") or ""
        print(f"  styles с jc: {style_jc}")

    for fname in footer_names:
        print(f"\n  -- {fname} --")
        ftext_xml = zf.read(fname)
        froot = ET.fromstring(ftext_xml)
        for p_idx, p in enumerate(froot.findall(f".//{{{W_NS}}}p")):
            ppr = p.find(f"{{{W_NS}}}pPr")
            jc_direct = None
            pstyle = None
            tabs_info: list[str] = []
            if ppr is not None:
                jc_el = ppr.find(f"{{{W_NS}}}jc")
                if jc_el is not None:
                    jc_direct = jc_el.get(f"{{{W_NS}}}val")
                ps_el = ppr.find(f"{{{W_NS}}}pStyle")
                if ps_el is not None:
                    pstyle = ps_el.get(f"{{{W_NS}}}val")
                tabs_el = ppr.find(f"{{{W_NS}}}tabs")
                if tabs_el is not None:
                    for t in tabs_el.findall(f"{{{W_NS}}}tab"):
                        tabs_info.append(
                            f"val={t.get(f'{{{W_NS}}}val')!r} pos={t.get(f'{{{W_NS}}}pos')!r}"
                        )
            jc_from_style = style_jc.get(pstyle or "")
            jc_effective = jc_direct or jc_from_style or "(default=left)"
            instr_texts: list[str] = []
            for it in p.findall(f".//{{{W_NS}}}instrText"):
                if it.text:
                    instr_texts.append(it.text.strip())
            simples: list[str] = []
            for fs in p.findall(f".//{{{W_NS}}}fldSimple"):
                instr = fs.get(f"{{{W_NS}}}instr") or ""
                simples.append(instr.strip())
            text_parts: list[str] = []
            for t in p.findall(f".//{{{W_NS}}}t"):
                if t.text:
                    text_parts.append(t.text)
            tab_count = len(p.findall(f".//{{{W_NS}}}tab"))
            joined_text = "".join(text_parts)
            has_page = any("PAGE" in s.upper() for s in instr_texts) or any(
                "PAGE" in s.upper() for s in simples
            )
            print(
                f"    p[{p_idx}] pStyle={pstyle!r} jc_direct={jc_direct!r} "
                f"jc_from_style={jc_from_style!r} jc_effective={jc_effective!r} "
                f"tabs_in_pPr={tabs_info} tab_runs_in_p={tab_count} "
                f"text={joined_text!r} instr={instr_texts!r} "
                f"fldSimple={simples!r} → has_PAGE={has_page}"
            )


def main() -> int:
    cfg = load_config()
    creds = Credentials.from_service_account_file(
        str(cfg.google_service_account_json),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    print(f"Качаю DOCX file_id={DISSERTATION_FILE_ID} (Камзебаева, row 2)...")
    blob = _download_bytes(drive, DISSERTATION_FILE_ID)
    print(f"Размер файла: {len(blob)} байт")

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        _dump_pgmar(zf)
        _dump_footers(zf)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
