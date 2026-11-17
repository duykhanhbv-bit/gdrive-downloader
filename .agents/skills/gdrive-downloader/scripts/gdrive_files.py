# -*- coding: utf-8 -*-
"""Generic Drive file downloader: virus-scan confirm=t, view-only PDF capture, and recursive Folder crawler."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from bs4 import BeautifulSoup

from gdrive_auth import DEFAULT_UA, build_requests_session, write_netscape_cookies
from gdrive_common import (
    add_query_params,
    classify_html_block,
    ensure_unique_path,
    extract_filename_from_cd,
    sanitize_filename,
)
from gdrive_media import download_drive_video_playwright, try_ytdlp_download


VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}


def parse_virus_scan_form(html_text: str) -> Optional[str]:
    """Extract the direct download URL + uuid/confirm tokens from Google Drive's virus scan warning page."""
    soup = BeautifulSoup(html_text, "lxml")
    form = soup.find("form", id="download-form") or soup.find("form", action=re.compile(r"download"))
    if form and form.get("action"):
        action = form["action"]
        if action.startswith("/"):
            action = "https://drive.usercontent.google.com" + action
        params: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                params[name] = inp.get("value", "")
        params.setdefault("confirm", "t")
        parsed = urllib.parse.urlparse(action)
        qs = urllib.parse.parse_qs(parsed.query)
        for k, v in params.items():
            qs[k] = [v]
        return urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path, parsed.params,
            urllib.parse.urlencode(qs, doseq=True), parsed.fragment,
        ))

    m = re.search(r'href="(/uc\?export=download[^"]+)"', html_text)
    if m:
        raw = m.group(1).replace("&amp;", "&")
        url = "https://drive.google.com" + raw
        if "confirm=" not in url:
            url += "&confirm=t"
        return url
    return None


def probe_drive_view_metadata(
    file_id: str,
    session,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
) -> tuple[str, str]:
    """Inspect /view HTML metadata to infer title and file category ('video'|'pdf'|'other')."""
    view_url = add_query_params(
        f"https://drive.google.com/file/d/{file_id}/view",
        resourcekey=resourcekey, authuser=authuser,
    )
    try:
        r = session.get(view_url, allow_redirects=True, timeout=30)
        if r.status_code != 200:
            return "", "other"
        soup = BeautifulSoup(r.text, "lxml")
        title = soup.title.get_text(strip=True) if soup.title else ""
        lower_html = r.text.lower()
        og_type = ""
        meta_type = soup.find("meta", attrs={"property": "og:type"})
        if meta_type:
            og_type = (meta_type.get("content") or "").lower()
        ext = Path(sanitize_filename(title)).suffix.lower()
        if "video" in og_type or ext in VIDEO_EXTS or "videoplayback" in lower_html or "youtube.com/embed" in lower_html:
            return title, "video"
        if ext == ".pdf" or "application/pdf" in lower_html:
            return title, "pdf"
        return title, "other"
    except Exception:
        return "", "other"


def _stream_response_to_file(resp, out_dir: Path, fallback_name: str, base_name: Optional[str]) -> tuple[Optional[Path], Optional[str], bytes]:
    """Stream response while sniffing the first chunk to ensure we don't save an HTML error page."""
    it = resp.iter_content(chunk_size=65536)
    first_chunk = b""
    for chunk in it:
        if chunk:
            first_chunk = chunk
            break

    ct = resp.headers.get("Content-Type", "")
    block = classify_html_block(first_chunk, ct)
    if block is not None:
        rest = b"".join(c for c in it if c)
        return None, block, first_chunk + rest

    cd_name = extract_filename_from_cd(resp.headers.get("Content-Disposition", ""))
    if base_name:
        ext = Path(cd_name).suffix if cd_name else ""
        fname = sanitize_filename(base_name) + (ext if not base_name.endswith(ext) else "")
    elif cd_name:
        raw_p = Path(cd_name)
        fname = sanitize_filename(raw_p.stem) + raw_p.suffix
    else:
        fname = sanitize_filename(fallback_name)

    out_path = ensure_unique_path(out_dir / fname)
    with open(out_path, "wb") as f:
        if first_chunk:
            f.write(first_chunk)
        for chunk in it:
            if chunk:
                f.write(chunk)
    return out_path, None, b""


def _capture_view_only_pdf_playwright(
    file_id: str,
    output_dir: Path,
    cookies: list[dict],
    base_name: Optional[str] = None,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    headless: bool = False,
) -> dict:
    """Capture rendered PDF/image pages when Drive disables Download/Print/Copy on /view."""
    from playwright.sync_api import sync_playwright  # type: ignore

    view_url = add_query_params(
        f"https://drive.google.com/file/d/{file_id}/view",
        resourcekey=resourcekey, authuser=authuser,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    captured_imgs: list[bytes] = []
    seen_hashes: set[int] = set()
    page_title = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=DEFAULT_UA, viewport={"width": 1280, "height": 1600})
        if cookies:
            ctx.add_cookies(cookies)
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            if ("viewerng/img" in url or "drive-viewer" in url) and "image/" in ct:
                try:
                    body = resp.body()
                    if len(body) > 4096:
                        h = hash(body)
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            captured_imgs.append(body)
                except Exception:
                    pass

        page.on("response", on_response)
        page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4500)
        page_title = page.title() or ""

        # Scroll through the Drive PDF viewer to trigger lazy rendering of all pages
        stagnant = 0
        prev_count = len(captured_imgs)
        for _ in range(150):
            page.keyboard.press("PageDown")
            page.wait_for_timeout(600)
            if len(captured_imgs) == prev_count:
                stagnant += 1
                if stagnant >= 6:
                    break
            else:
                stagnant = 0
                prev_count = len(captured_imgs)

        # Fallback: screenshot rendered page elements if viewerng/img responses weren't intercepted
        if not captured_imgs:
            page_els = page.query_selector_all('[role="document"] img, .ndfHFb-c4YZDc-cYSp0e-DARUcf img, canvas')
            for el in page_els:
                try:
                    shot = el.screenshot(type="png")
                    if len(shot) > 4096:
                        h = hash(shot)
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            captured_imgs.append(shot)
                except Exception:
                    pass
        browser.close()

    if not captured_imgs:
        raise RuntimeError(
            "Unable to download file or capture view-only PDF pages (access denied or cookie expired)."
        )

    stem = sanitize_filename(base_name or page_title or f"view_only_{file_id}")
    out_pdf = ensure_unique_path(output_dir / f"{stem}.pdf")
    try:
        from PIL import Image  # type: ignore
        imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in captured_imgs]
        imgs[0].save(str(out_pdf), save_all=True, append_images=imgs[1:])
        return {
            "status": "ok",
            "path": str(out_pdf),
            "pages_captured": len(imgs),
            "size_bytes": out_pdf.stat().st_size,
            "method": "view_only_pdf_capture",
            "verified": True,
        }
    except ImportError:
        pages_dir = output_dir / stem
        pages_dir.mkdir(parents=True, exist_ok=True)
        for idx, b in enumerate(captured_imgs, 1):
            (pages_dir / f"page_{idx:03d}.png").write_bytes(b)
        return {
            "status": "ok",
            "path": str(pages_dir),
            "pages_captured": len(captured_imgs),
            "method": "view_only_png_pages",
            "verified": True,
        }


def download_drive_file(
    file_id: str,
    output_dir: Path,
    cookies: list[dict],
    base_name: Optional[str] = None,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    force_type: Optional[str] = None,
    headless: bool = False,
) -> dict:
    """Download any generic Drive file: direct/confirm=t -> gdown -> DASH video or view-only PDF fallback."""
    output_dir.mkdir(parents=True, exist_ok=True)
    session = build_requests_session(cookies)

    view_title, detected_kind = probe_drive_view_metadata(file_id, session, resourcekey, authuser)
    effective_name = base_name or (sanitize_filename(view_title) if view_title else None)
    kind = force_type or detected_kind

    # Tier 1: Direct binary download (gets original raw file when permitted)
    dl_url = add_query_params(
        "https://drive.usercontent.google.com/download",
        resourcekey=resourcekey, authuser=authuser,
        extra={"id": file_id, "export": "download", "confirm": "t"},
    )
    resp = session.get(dl_url, stream=True, allow_redirects=True, timeout=120)
    out_path, block, html_bytes = _stream_response_to_file(resp, output_dir, f"drive_{file_id}", effective_name)

    if block == "virus_scan_warning":
        confirm_url = parse_virus_scan_form(html_bytes.decode("utf-8", errors="ignore"))
        if confirm_url:
            resp2 = session.get(confirm_url, stream=True, allow_redirects=True, timeout=120)
            out_path, block, html_bytes = _stream_response_to_file(resp2, output_dir, f"drive_{file_id}", effective_name)

    if out_path is not None and out_path.exists() and out_path.stat().st_size > 0:
        return {
            "status": "ok",
            "path": str(out_path),
            "size_bytes": out_path.stat().st_size,
            "method": "direct_usercontent_download",
            "verified": True,
        }

    if block == "quota_exceeded":
        raise RuntimeError(
            "Google Drive 24h download quota exceeded for this file. "
            "Add a shortcut/Make a copy to your own Drive and download the copy."
        )

    # Tier 2: gdown fallback (handles public large files & confirm nuances)
    gdown_bin = shutil.which("gdown")
    if gdown_bin and block != "login_required" and kind != "video":
        with tempfile.TemporaryDirectory(prefix="gdrive_gdown_") as tmpdir:
            tmp_p = Path(tmpdir)
            cookie_txt = tmp_p / "cookies.txt"
            write_netscape_cookies(cookies, cookie_txt)
            target = add_query_params(
                f"https://drive.google.com/uc?id={file_id}",
                resourcekey=resourcekey, authuser=authuser,
            )
            res = subprocess.run(
                [gdown_bin, "--fuzzy", target, "-O", str(tmp_p) + os.sep],
                capture_output=True, text=True, timeout=180,
            )
            dl_files = [f for f in tmp_p.iterdir() if f.is_file() and f.name != "cookies.txt"]
            if res.returncode == 0 and dl_files:
                src = dl_files[0]
                if effective_name:
                    base_stem = sanitize_filename(effective_name)
                    out_name = base_stem if base_stem.lower().endswith(src.suffix.lower()) else f"{base_stem}{src.suffix}"
                else:
                    out_name = src.name
                dest = ensure_unique_path(output_dir / out_name)
                shutil.move(str(src), str(dest))
                return {
                    "status": "ok",
                    "path": str(dest),
                    "size_bytes": dest.stat().st_size,
                    "method": "gdown_fallback",
                    "verified": True,
                }

    # Tier 3: View-only / download-disabled fallbacks
    if kind == "video":
        view_url = add_query_params(
            f"https://drive.google.com/file/d/{file_id}/view",
            resourcekey=resourcekey, authuser=authuser,
        )
        yt_out = try_ytdlp_download(view_url, output_dir, effective_name, cookies)
        if yt_out is not None:
            return {
                "status": "ok",
                "path": str(yt_out),
                "size_bytes": yt_out.stat().st_size,
                "method": "ytdlp_video",
                "verified": True,
            }
        return download_drive_video_playwright(
            file_id=file_id,
            output_dir=output_dir,
            cookies=cookies,
            base_name=effective_name,
            resourcekey=resourcekey,
            authuser=authuser,
            headless=headless,
        )

    if kind == "pdf":
        return _capture_view_only_pdf_playwright(
            file_id=file_id,
            output_dir=output_dir,
            cookies=cookies,
            base_name=effective_name,
            resourcekey=resourcekey,
            authuser=authuser,
            headless=headless,
        )

    raise RuntimeError(
        f"Direct download blocked ({block or 'http_' + str(resp.status_code)}). "
        "If this is a view-only video pass `--type video`; if view-only PDF pass `--type pdf`; "
        "otherwise refresh your Google login in Firefox."
    )


def download_drive_folder(
    folder_id: str,
    output_dir: Path,
    cookies: list[dict],
    dispatch_fn: Callable[[str, Path], dict],
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    headless: bool = True,
    visited: Optional[set[str]] = None,
) -> dict:
    """Recursively crawl and download all files & subfolders in a Google Drive folder (cycle-safe)."""
    from playwright.sync_api import sync_playwright  # type: ignore

    if visited is None:
        visited = set()
    if folder_id in visited:
        return {"status": "skipped_cycle", "folder_id": folder_id, "items": []}
    visited.add(folder_id)

    folder_url = add_query_params(
        f"https://drive.google.com/drive/folders/{folder_id}",
        resourcekey=resourcekey, authuser=authuser,
    )

    items: list[dict] = []
    folder_title = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(user_agent=DEFAULT_UA, viewport={"width": 1400, "height": 900})
        if cookies:
            ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(folder_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4500)
        folder_title = sanitize_filename(page.title() or f"folder_{folder_id}")

        # Scroll folder list to materialize virtualized items
        prev_len = -1
        for _ in range(30):
            cur_len = page.evaluate("() => document.querySelectorAll('[data-id]').length")
            if cur_len == prev_len:
                break
            prev_len = cur_len
            page.keyboard.press("End")
            page.wait_for_timeout(800)

        items = page.evaluate(
            """() => {
                const seen = new Set();
                const out = [];
                document.querySelectorAll('[data-id]').forEach(el => {
                    const id = el.getAttribute('data-id');
                    if (!id || seen.has(id) || id.startsWith('_')) return;
                    seen.add(id);
                    const target = el.getAttribute('data-target') || '';
                    const labelEl = el.querySelector('[data-tooltip]') || el.querySelector('[aria-label]') || el;
                    const rawText = (el.innerText || '').split('\\n')[0].trim();
                    const aria = (el.getAttribute('aria-label') || labelEl.getAttribute('aria-label') || '').toLowerCase();
                    const isFolder = target === 'folder' || aria.includes('folder') || aria.includes('thư mục');
                    let kind = 'drive_file';
                    if (isFolder) kind = 'folder';
                    else if (aria.includes('google docs') || aria.includes('tài liệu')) kind = 'gdoc';
                    else if (aria.includes('google sheets') || aria.includes('trang tính')) kind = 'gsheet';
                    else if (aria.includes('google slides') || aria.includes('trang trình bày')) kind = 'gslide';
                    out.push({ id, name: rawText || id, kind });
                });
                return out;
            }"""
        )
        browser.close()

    target_dir = output_dir / folder_title
    target_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in items:
        cid = item["id"]
        ckind = item["kind"]
        if ckind == "folder":
            sub_res = download_drive_folder(
                cid, target_dir, cookies, dispatch_fn,
                resourcekey=resourcekey, authuser=authuser, headless=headless, visited=visited,
            )
            results.append(sub_res)
        else:
            if ckind == "gdoc":
                child_url = f"https://docs.google.com/document/d/{cid}/edit"
            elif ckind == "gsheet":
                child_url = f"https://docs.google.com/spreadsheets/d/{cid}/edit"
            elif ckind == "gslide":
                child_url = f"https://docs.google.com/presentation/d/{cid}/edit"
            else:
                child_url = f"https://drive.google.com/file/d/{cid}/view"
            try:
                res = dispatch_fn(child_url, target_dir)
                results.append(res)
            except Exception as exc:
                results.append({"status": "error", "id": cid, "name": item["name"], "error": str(exc)})

    return {
        "status": "ok",
        "folder": str(target_dir),
        "items_found": len(items),
        "results": results,
    }
