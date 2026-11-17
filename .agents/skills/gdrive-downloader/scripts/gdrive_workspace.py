# -*- coding: utf-8 -*-
"""Google Workspace downloader: Docs (multi-tab + view-only), Sheets (xlsx/csv + htmlview/gviz), Slides."""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import urllib.parse
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag

from gdrive_auth import DEFAULT_UA, build_requests_session
from gdrive_common import (
    add_query_params,
    classify_html_block,
    ensure_unique_path,
    extract_filename_from_cd,
    sanitize_filename,
)


BAD_DOC_MARKERS = (
    "DOCS_initDocsMobileWeb",
    "navigator.sendBeacon",
    "enterCoreJsErrorDialog",
    "counter-increment",
)


def add_docx_page_numbering(doc) -> None:
    """Attach mandatory automatic PAGE field centered in footer, suppressed on first page."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    from docx.shared import Pt

    for section in doc.sections:
        section.different_first_page_header_footer = True
        p = section.footer.paragraphs[0] if section.footer.paragraphs else section.footer.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        fld = parse_xml(
            f'<w:fldSimple {nsdecls("w")} w:instr="PAGE">'
            f'<w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'
            f'<w:sz w:val="24"/></w:rPr><w:t>1</w:t></w:r></w:fldSimple>'
        )
        p._p.append(fld)


def markdown_to_docx(md_text: str, out_path: Path, title: str = "") -> Path:
    """Convert extracted Markdown text into a clean Word .docx with mandatory page numbering."""
    import docx
    from docx.shared import Inches, Pt

    doc = docx.Document()
    for sec in doc.sections:
        sec.top_margin = Inches(0.8)
        sec.bottom_margin = Inches(0.8)
        sec.left_margin = Inches(1.0)
        sec.right_margin = Inches(0.8)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)

    if title:
        h = doc.add_heading(title, level=0)
        for run in h.runs:
            run.font.name = "Times New Roman"

    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|\s*:?-{2,}", lines[i + 1].strip()):
            table_lines = [line]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            rows_data = [[c.strip() for c in row.strip("|").split("|")] for row in table_lines]
            if rows_data:
                cols = max(len(r) for r in rows_data)
                tbl = doc.add_table(rows=len(rows_data), cols=cols)
                tbl.style = "Table Grid"
                for r_idx, row in enumerate(rows_data):
                    for c_idx, val in enumerate(row):
                        tbl.cell(r_idx, c_idx).text = val.replace("<br>", "\n")
            continue
        elif line.startswith(("* ", "- ")):
            doc.add_paragraph(line[2:].strip(), style="List Bullet")
        elif re.match(r"^\d+\.\s+", line):
            doc.add_paragraph(re.sub(r"^\d+\.\s+", "", line), style="List Number")
        else:
            doc.add_paragraph(line)
        i += 1

    add_docx_page_numbering(doc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def clean_gdoc_html_to_markdown(html_content: str) -> tuple[str, str]:
    """Parse Google Docs HTML/mobilebasic cleanly, stripping all script/style/JS artifacts."""
    soup = BeautifulSoup(html_content, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""

    # Strip all script/style/noscript/meta/link BEFORE traversal to avoid JS/CSS leakage
    for bad_tag in soup.find_all(["script", "style", "noscript", "meta", "link", "svg"]):
        bad_tag.decompose()

    def _walk(node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        tag = node.name.lower()
        if tag == "a":
            href = node.get("href", "")
            text = "".join(_walk(c) for c in node.children).strip()
            if "google.com/url?q=" in href:
                href = urllib.parse.unquote(href.split("google.com/url?q=")[1].split("&")[0])
            if href and text:
                return f"[{text}]({href})"
            return text or (f"<{href}>" if href else "")
        if tag in ("b", "strong"):
            inner = "".join(_walk(c) for c in node.children).strip()
            return f"**{inner}** " if inner else ""
        if tag in ("i", "em"):
            inner = "".join(_walk(c) for c in node.children).strip()
            return f"*{inner}* " if inner else ""
        if tag == "span":
            style = (node.get("style") or "").lower()
            inner = "".join(_walk(c) for c in node.children)
            if ("font-weight:700" in style or "font-weight:bold" in style) and inner.strip():
                return f"**{inner.strip()}** "
            return inner
        if tag == "br":
            return "\n"
        if tag in ("ul", "ol"):
            items = []
            is_ol = tag == "ol"
            idx = 1
            for li in node.find_all("li", recursive=False):
                txt = "".join(_walk(c) for c in li.children).strip()
                if txt:
                    items.append(f"{idx}. {txt}" if is_ol else f"* {txt}")
                    idx += 1
            return "\n" + "\n".join(items) + "\n"
        if tag == "table":
            rows = []
            for tr in node.find_all("tr"):
                cells = []
                for td in tr.find_all(["td", "th"]):
                    c_txt = "".join(_walk(c) for c in td.children).strip().replace("\n", "<br>")
                    cells.append(c_txt)
                if cells:
                    rows.append(cells)
            if not rows:
                return ""
            cols = max(len(r) for r in rows)
            norm = [r + [""] * (cols - len(r)) for r in rows]
            md_tbl = [
                "| " + " | ".join(norm[0]) + " |",
                "| " + " | ".join([":---"] * cols) + " |",
            ]
            for r in norm[1:]:
                md_tbl.append("| " + " | ".join(r) + " |")
            return "\n\n" + "\n".join(md_tbl) + "\n\n"
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            inner = "".join(_walk(c) for c in node.children).strip()
            if not inner:
                return ""
            level = int(tag[1])
            return f"\n\n{'#' * level} {inner}\n\n"
        if tag in ("p", "div"):
            inner = "".join(_walk(c) for c in node.children).strip()
            return f"\n\n{inner}\n\n" if inner else ""
        return "".join(_walk(c) for c in node.children)

    body = soup.find("body") or soup
    raw_md = _walk(body)
    clean_md = re.sub(r"\n{3,}", "\n\n", raw_md).strip()
    for bad in BAD_DOC_MARKERS:
        if bad in clean_md:
            clean_md = "\n".join(ln for ln in clean_md.splitlines() if bad not in ln)
    return title, clean_md


def extract_docs_model_chunks(html_text: str) -> str:
    """Extract embedded text runs from Google Docs DOCS_modelChunk script blocks (canvas-rendered docs)."""
    chunks: list[str] = []
    for m in re.finditer(r"DOCS_modelChunk\s*=\s*(\[.*?\]);", html_text, re.DOTALL):
        try:
            arr = json.loads(m.group(1))
            for item in arr:
                if isinstance(item, dict) and isinstance(item.get("s"), str):
                    chunks.append(item["s"])
        except Exception:
            continue
    return "".join(chunks).strip()


def _validate_binary_export(data: bytes, fmt: str, content_type: str) -> bool:
    if not data:
        return False
    if fmt == "docx":
        return data[:2] == b"PK"
    if fmt == "xlsx":
        return data[:2] == b"PK"
    if fmt == "pptx":
        return data[:2] == b"PK"
    if fmt == "pdf":
        return data[:5] == b"%PDF-"
    block = classify_html_block(data, content_type)
    if fmt in ("txt", "md", "csv", "tsv"):
        return block is None
    if fmt == "html":
        return block not in ("login_required", "permission_denied", "quota_exceeded")
    return block is None


def _playwright_fetch_url(
    page_url: str,
    fetch_url: str,
    cookies: list[dict],
    headless: bool = True,
) -> tuple[bool, bytes, str]:
    """Use Playwright browser context to run same-origin fetch() and return (ok, bytes, page_title)."""
    from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=DEFAULT_UA)
        if cookies:
            ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        title = page.title() or ""
        res = page.evaluate(
            """async (targetUrl) => {
                const resp = await fetch(targetUrl, { credentials: 'include' });
                const ct = resp.headers.get('content-type') || '';
                if (!resp.ok) return { ok: false, status: resp.status, ct };
                const buf = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let bin = '';
                const step = 0x8000;
                for (let i = 0; i < bytes.length; i += step) {
                    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
                }
                return { ok: true, status: resp.status, ct, b64: btoa(bin) };
            }""",
            fetch_url,
        )
        browser.close()
    if res.get("ok") and res.get("b64"):
        return True, base64.b64decode(res["b64"]), title
    return False, b"", title


def _playwright_scrape_gdoc_view_only(
    doc_url: str,
    cookies: list[dict],
    headless: bool = True,
) -> tuple[str, str]:
    """Fallback when Google Doc disables export/download: scrape DOCS_modelChunk + tabs + scrolled DOM."""
    from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=DEFAULT_UA, viewport={"width": 1400, "height": 900})
        if cookies:
            ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(doc_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        title = page.title() or ""
        html_raw = page.content()

        model_text = extract_docs_model_chunks(html_raw)
        if len(model_text) > 100:
            browser.close()
            return title, model_text

        # Multi-tab + DOM scrolling extraction
        sections: list[str] = []
        tab_els = page.query_selector_all('[role="tab"], .chapter-item, .docs-revisions-tile')
        if not tab_els:
            tab_els = [None]

        for idx, tab_el in enumerate(tab_els):
            tab_name = ""
            if tab_el is not None:
                try:
                    tab_name = (tab_el.inner_text() or "").strip()
                    tab_el.click()
                    page.wait_for_timeout(1500)
                except Exception:
                    pass

            collected: list[str] = []
            seen: set[str] = set()
            stagnant = 0
            for _ in range(100):
                lines = page.evaluate(
                    """() => {
                        const out = [];
                        const els = document.querySelectorAll(
                            '.kix-lineview-text-block, .kix-paragraphrenderer, .kix-word-html, svg text'
                        );
                        els.forEach(el => {
                            const t = (el.innerText || el.textContent || '').trim();
                            if (t) out.push(t);
                        });
                        return out;
                    }"""
                )
                added = 0
                for ln in lines:
                    if ln not in seen:
                        seen.add(ln)
                        collected.append(ln)
                        added += 1
                if added == 0:
                    stagnant += 1
                    if stagnant >= 4:
                        break
                else:
                    stagnant = 0
                page.keyboard.press("PageDown")
                page.wait_for_timeout(250)

            if collected:
                header = f"## {tab_name or f'Tab {idx + 1}'}\n\n" if len(tab_els) > 1 else ""
                sections.append(header + "\n".join(collected))

        browser.close()
        return title, "\n\n".join(sections).strip()


def download_gdoc(
    doc_id: str,
    output_dir: Path,
    cookies: list[dict],
    fmt: str = "docx",
    base_name: Optional[str] = None,
    tab: Optional[str] = None,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    headless: bool = True,
) -> dict:
    """Download a Google Doc via Tier 1 export -> Tier 2 Playwright fetch -> Tier 3 mobilebasic/DOM."""
    fmt = fmt.lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    session = build_requests_session(cookies)

    export_extra = {"format": fmt}
    if tab:
        export_extra["tab"] = tab
    export_url = add_query_params(
        f"https://docs.google.com/document/d/{doc_id}/export",
        resourcekey=resourcekey, authuser=authuser, extra=export_extra,
    )

    # Tier 1: Direct authenticated export
    resp = session.get(export_url, allow_redirects=True, timeout=60)
    ct = resp.headers.get("Content-Type", "")
    if resp.status_code == 200 and _validate_binary_export(resp.content, fmt, ct):
        cd_name = extract_filename_from_cd(resp.headers.get("Content-Disposition", ""))
        stem = sanitize_filename(base_name or (Path(cd_name).stem if cd_name else f"gdoc_{doc_id}"))
        out_path = ensure_unique_path(output_dir / f"{stem}.{fmt}")
        out_path.write_bytes(resp.content)
        return {
            "status": "ok",
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "method": "direct_export",
            "verified": True,
        }

    # Tier 2: In-page Playwright fetch
    edit_url = add_query_params(
        f"https://docs.google.com/document/d/{doc_id}/edit",
        resourcekey=resourcekey, authuser=authuser,
        extra={"tab": tab} if tab else None,
    )
    ok, raw_bytes, pg_title = _playwright_fetch_url(edit_url, export_url, cookies, headless=headless)
    if ok and _validate_binary_export(raw_bytes, fmt, ""):
        stem = sanitize_filename(base_name or pg_title or f"gdoc_{doc_id}")
        out_path = ensure_unique_path(output_dir / f"{stem}.{fmt}")
        out_path.write_bytes(raw_bytes)
        return {
            "status": "ok",
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "method": "playwright_inpage_export",
            "verified": True,
        }

    # Tier 3: View-only fallback (/mobilebasic -> Playwright DOCS_modelChunk/DOM)
    mb_url = add_query_params(
        f"https://docs.google.com/document/d/{doc_id}/mobilebasic",
        resourcekey=resourcekey, authuser=authuser,
    )
    mb_resp = session.get(mb_url, allow_redirects=True, timeout=60)
    extracted_title, extracted_md = "", ""
    if mb_resp.status_code == 200 and classify_html_block(mb_resp.content, "text/html") not in ("login_required", "permission_denied"):
        extracted_title, extracted_md = clean_gdoc_html_to_markdown(mb_resp.text)

    if len(extracted_md) < 50:
        pw_title, pw_text = _playwright_scrape_gdoc_view_only(edit_url, cookies, headless=headless)
        if pw_text:
            extracted_title = extracted_title or pw_title
            extracted_md = pw_text

    if not extracted_md:
        raise RuntimeError(
            "Unable to export or scrape Google Doc (access denied, login expired, or canvas-protected)."
        )

    stem = sanitize_filename(base_name or extracted_title or pg_title or f"gdoc_{doc_id}")
    md_path = ensure_unique_path(output_dir / f"{stem}.md")
    md_path.write_text(extracted_md, encoding="utf-8")

    outputs = [str(md_path)]
    if fmt == "docx":
        docx_path = ensure_unique_path(output_dir / f"{stem}.docx")
        markdown_to_docx(extracted_md, docx_path, title=stem)
        outputs.append(str(docx_path))

    primary = Path(outputs[-1])
    return {
        "status": "ok",
        "path": str(primary),
        "extra_paths": outputs,
        "size_bytes": primary.stat().st_size,
        "method": "view_only_mobilebasic_dom_fallback",
        "verified": True,
    }


def parse_htmlview_to_sheets(html_text: str) -> dict[str, list[list[str]]]:
    """Parse Google Sheets /htmlview or /pubhtml HTML tables (.waffle) into {sheet_name: rows}."""
    soup = BeautifulSoup(html_text, "lxml")
    sheet_names: dict[str, str] = {}

    # Sheet menu buttons/tabs map sheetId -> sheetName
    for li in soup.select("#sheet-menu li"):
        sid = (li.get("id") or "").replace("sheet-button-", "")
        name = li.get_text(strip=True)
        if sid and name:
            sheet_names[sid] = name

    # Fallback regex from embedded script items.push({name: "...", id: "..."})
    if not sheet_names:
        for m in re.finditer(r'name:\s*"([^"]+)"\s*,\s*pageUrl:[^}]+?id:\s*"([^"]+)"', html_text):
            sheet_names[m.group(2)] = m.group(1)

    sheets: dict[str, list[list[str]]] = {}
    tables = soup.select("table.waffle") or soup.find_all("table")
    for idx, tbl in enumerate(tables):
        parent_div = tbl.find_parent(id=True)
        div_id = parent_div.get("id", "") if parent_div else ""
        sname = sheet_names.get(div_id) or (list(sheet_names.values())[idx] if idx < len(sheet_names) else f"Sheet{idx + 1}")

        rows: list[list[str]] = []
        for tr in tbl.find_all("tr"):
            cells: list[str] = []
            for td in tr.find_all(["td", "th"]):
                # Skip row-header index cells if labeled th.row-header-shim
                classes = td.get("class") or []
                if "row-header-shim" in classes or "freezebar-cell" in classes:
                    continue
                txt = td.get_text(" ", strip=True)
                colspan = int(td.get("colspan") or 1)
                cells.append(txt)
                for _ in range(colspan - 1):
                    cells.append("")
            if any(c != "" for c in cells):
                rows.append(cells)
        if rows:
            sheets[sname] = rows
    return sheets


def build_xlsx_from_sheets(sheets: dict[str, list[list[str]]], out_path: Path) -> Path:
    """Build and verify a multi-sheet .xlsx workbook via openpyxl."""
    import openpyxl

    wb = openpyxl.Workbook()
    default_ws = wb.active
    used_names: set[str] = set()

    for idx, (raw_name, rows) in enumerate(sheets.items()):
        safe_title = re.sub(r'[:\\/?*\[\]]', "_", raw_name)[:31] or f"Sheet{idx + 1}"
        while safe_title in used_names:
            safe_title = (safe_title[:28] + f"_{idx}")[:31]
        used_names.add(safe_title)
        ws = default_ws if idx == 0 else wb.create_sheet(title=safe_title)
        ws.title = safe_title
        for row in rows:
            ws.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))
    # Verify workbook opens cleanly
    check_wb = openpyxl.load_workbook(str(out_path), read_only=True)
    check_wb.close()
    return out_path


def download_gsheet(
    sheet_id: str,
    output_dir: Path,
    cookies: list[dict],
    fmt: str = "xlsx",
    gid: Optional[str] = None,
    base_name: Optional[str] = None,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    headless: bool = True,
) -> dict:
    """Download a Google Sheet via /export -> Playwright fetch -> /gviz -> /htmlview DOM rebuild."""
    fmt = fmt.lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    session = build_requests_session(cookies)

    extra = {"format": fmt}
    if fmt in ("csv", "tsv") and gid is not None:
        extra["gid"] = gid
    export_url = add_query_params(
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export",
        resourcekey=resourcekey, authuser=authuser, extra=extra,
    )

    # Tier 1: Direct export
    resp = session.get(export_url, allow_redirects=True, timeout=60)
    ct = resp.headers.get("Content-Type", "")
    if resp.status_code == 200 and _validate_binary_export(resp.content, fmt, ct):
        cd_name = extract_filename_from_cd(resp.headers.get("Content-Disposition", ""))
        stem = sanitize_filename(base_name or (Path(cd_name).stem if cd_name else f"gsheet_{sheet_id}"))
        out_path = ensure_unique_path(output_dir / f"{stem}.{fmt}")
        out_path.write_bytes(resp.content)
        return {
            "status": "ok",
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "method": "direct_export",
            "verified": True,
        }

    # Tier 2: Playwright in-page fetch
    edit_url = add_query_params(
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
        resourcekey=resourcekey, authuser=authuser,
    )
    ok, raw_bytes, pg_title = _playwright_fetch_url(edit_url, export_url, cookies, headless=headless)
    if ok and _validate_binary_export(raw_bytes, fmt, ""):
        stem = sanitize_filename(base_name or pg_title or f"gsheet_{sheet_id}")
        out_path = ensure_unique_path(output_dir / f"{stem}.{fmt}")
        out_path.write_bytes(raw_bytes)
        return {
            "status": "ok",
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "method": "playwright_inpage_export",
            "verified": True,
        }

    # Tier 3A: gviz CSV endpoint (often open even when export is blocked)
    if fmt == "csv":
        gviz_url = add_query_params(
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq",
            resourcekey=resourcekey, authuser=authuser,
            extra={"tqx": "out:csv", "gid": gid or "0"},
        )
        gresp = session.get(gviz_url, allow_redirects=True, timeout=60)
        if gresp.status_code == 200 and classify_html_block(gresp.content, gresp.headers.get("Content-Type", "")) is None:
            stem = sanitize_filename(base_name or pg_title or f"gsheet_{sheet_id}")
            out_path = ensure_unique_path(output_dir / f"{stem}.csv")
            out_path.write_bytes(gresp.content)
            return {
                "status": "ok",
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size,
                "method": "gviz_csv_fallback",
                "verified": True,
            }

    # Tier 3B: /htmlview table extraction across all sheet tabs
    hv_url = add_query_params(
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview",
        resourcekey=resourcekey, authuser=authuser,
    )
    hv_resp = session.get(hv_url, allow_redirects=True, timeout=60)
    html_text = hv_resp.text if hv_resp.status_code == 200 else ""
    sheets_data = parse_htmlview_to_sheets(html_text) if html_text else {}

    if not sheets_data:
        from playwright.sync_api import sync_playwright  # type: ignore
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(user_agent=DEFAULT_UA)
            if cookies:
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto(hv_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            pg_title = pg_title or page.title()
            sheets_data = parse_htmlview_to_sheets(page.content())
            browser.close()

    if not sheets_data:
        raise RuntimeError("Unable to export or scrape Google Sheet (access denied or empty).")

    stem = sanitize_filename(base_name or pg_title or f"gsheet_{sheet_id}")
    if fmt == "csv":
        first_rows = next(iter(sheets_data.values()))
        out_path = ensure_unique_path(output_dir / f"{stem}.csv")
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(first_rows)
    else:
        out_path = ensure_unique_path(output_dir / f"{stem}.xlsx")
        build_xlsx_from_sheets(sheets_data, out_path)

    return {
        "status": "ok",
        "path": str(out_path),
        "sheets_extracted": list(sheets_data.keys()),
        "size_bytes": out_path.stat().st_size,
        "method": "view_only_htmlview_fallback",
        "verified": True,
    }


def download_gslide(
    pres_id: str,
    output_dir: Path,
    cookies: list[dict],
    fmt: str = "pptx",
    base_name: Optional[str] = None,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    headless: bool = True,
) -> dict:
    """Download Google Slides via /export -> Playwright fetch -> Playwright /present slide capture."""
    fmt = fmt.lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    session = build_requests_session(cookies)

    export_url = add_query_params(
        f"https://docs.google.com/presentation/d/{pres_id}/export/{fmt}",
        resourcekey=resourcekey, authuser=authuser,
    )
    resp = session.get(export_url, allow_redirects=True, timeout=60)
    if resp.status_code == 200 and _validate_binary_export(resp.content, fmt, resp.headers.get("Content-Type", "")):
        cd_name = extract_filename_from_cd(resp.headers.get("Content-Disposition", ""))
        stem = sanitize_filename(base_name or (Path(cd_name).stem if cd_name else f"gslide_{pres_id}"))
        out_path = ensure_unique_path(output_dir / f"{stem}.{fmt}")
        out_path.write_bytes(resp.content)
        return {
            "status": "ok",
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "method": "direct_export",
            "verified": True,
        }

    edit_url = add_query_params(
        f"https://docs.google.com/presentation/d/{pres_id}/edit",
        resourcekey=resourcekey, authuser=authuser,
    )
    ok, raw_bytes, pg_title = _playwright_fetch_url(edit_url, export_url, cookies, headless=headless)
    if ok and _validate_binary_export(raw_bytes, fmt, ""):
        stem = sanitize_filename(base_name or pg_title or f"gslide_{pres_id}")
        out_path = ensure_unique_path(output_dir / f"{stem}.{fmt}")
        out_path.write_bytes(raw_bytes)
        return {
            "status": "ok",
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "method": "playwright_inpage_export",
            "verified": True,
        }

    # Tier 3: View-only slide PNG capture -> PDF
    from playwright.sync_api import sync_playwright  # type: ignore

    embed_url = add_query_params(
        f"https://docs.google.com/presentation/d/{pres_id}/embed",
        resourcekey=resourcekey, authuser=authuser,
    )
    png_bytes_list: list[bytes] = []
    slide_texts: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=DEFAULT_UA, viewport={"width": 1280, "height": 720})
        if cookies:
            ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(embed_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        pg_title = pg_title or page.title()

        prev_hash = None
        for idx in range(120):
            shot = page.screenshot(type="png")
            cur_hash = hash(shot)
            if cur_hash == prev_hash:
                break
            prev_hash = cur_hash
            png_bytes_list.append(shot)
            txt = page.inner_text("body") or ""
            slide_texts.append(f"## Slide {idx + 1}\n\n{txt.strip()}")
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(800)
        browser.close()

    if not png_bytes_list:
        raise RuntimeError("Unable to export or capture Google Slides.")

    stem = sanitize_filename(base_name or pg_title or f"gslide_{pres_id}")
    out_pdf = ensure_unique_path(output_dir / f"{stem}.pdf")
    try:
        from PIL import Image  # type: ignore
        imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in png_bytes_list]
        imgs[0].save(str(out_pdf), save_all=True, append_images=imgs[1:])
    except ImportError:
        slide_dir = output_dir / stem
        slide_dir.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(png_bytes_list, 1):
            (slide_dir / f"slide_{i:03d}.png").write_bytes(b)
        out_pdf = slide_dir

    md_notes = ensure_unique_path(output_dir / f"{stem}_text.md")
    md_notes.write_text("\n\n".join(slide_texts), encoding="utf-8")

    return {
        "status": "ok",
        "path": str(out_pdf),
        "extra_paths": [str(md_notes)],
        "slides_captured": len(png_bytes_list),
        "method": "view_only_slide_capture",
        "verified": True,
    }
