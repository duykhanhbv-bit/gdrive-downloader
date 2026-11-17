# -*- coding: utf-8 -*-
"""Unified CLI entrypoint for downloading any Google Drive / Docs / Sheets / Slides / Folder URL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gdrive_auth import load_cookies
from gdrive_common import parse_drive_url
from gdrive_files import download_drive_file, download_drive_folder
from gdrive_media import download_drive_video_playwright, try_ytdlp_download
from gdrive_workspace import download_gdoc, download_gsheet, download_gslide


def dispatch_download(
    url: str,
    output_dir: Path,
    cookies: list[dict],
    fmt: Optional[str] = None,
    name: Optional[str] = None,
    force_type: Optional[str] = None,
    authuser: Optional[str] = None,
    headless: bool = False,
) -> dict:
    """Route a Google Drive/Workspace URL to the appropriate waterfall downloader."""
    parsed = parse_drive_url(url)
    effective_auth = authuser if authuser is not None else parsed.authuser
    kind = force_type if force_type in ("gdoc", "gsheet", "gslide", "folder") else parsed.kind

    if not parsed.file_id:
        raise ValueError(f"Unrecognized Google Drive/Workspace URL or ID: {url}")

    if kind == "gdoc":
        return download_gdoc(
            doc_id=parsed.file_id,
            output_dir=output_dir,
            cookies=cookies,
            fmt=fmt or "docx",
            base_name=name,
            tab=parsed.tab,
            resourcekey=parsed.resourcekey,
            authuser=effective_auth,
            headless=headless,
        )

    if kind == "gsheet":
        return download_gsheet(
            sheet_id=parsed.file_id,
            output_dir=output_dir,
            cookies=cookies,
            fmt=fmt or "xlsx",
            gid=parsed.gid,
            base_name=name,
            resourcekey=parsed.resourcekey,
            authuser=effective_auth,
            headless=headless,
        )

    if kind == "gslide":
        return download_gslide(
            pres_id=parsed.file_id,
            output_dir=output_dir,
            cookies=cookies,
            fmt=fmt or "pptx",
            base_name=name,
            resourcekey=parsed.resourcekey,
            authuser=effective_auth,
            headless=headless,
        )

    if kind == "folder":
        return download_drive_folder(
            folder_id=parsed.file_id,
            output_dir=output_dir,
            cookies=cookies,
            dispatch_fn=lambda child_url, sub_dir: dispatch_download(
                child_url, sub_dir, cookies, fmt=fmt, authuser=effective_auth, headless=headless
            ),
            resourcekey=parsed.resourcekey,
            authuser=effective_auth,
            headless=headless,
        )

    if force_type == "video":
        yt_out = try_ytdlp_download(url, output_dir, name, cookies)
        if yt_out is not None:
            return {
                "status": "ok",
                "path": str(yt_out),
                "size_bytes": yt_out.stat().st_size,
                "method": "ytdlp_video",
                "verified": True,
            }
        return download_drive_video_playwright(
            file_id=parsed.file_id,
            output_dir=output_dir,
            cookies=cookies,
            base_name=name,
            resourcekey=parsed.resourcekey,
            authuser=effective_auth,
            headless=headless,
        )

    return download_drive_file(
        file_id=parsed.file_id,
        output_dir=output_dir,
        cookies=cookies,
        base_name=name,
        resourcekey=parsed.resourcekey,
        authuser=effective_auth,
        force_type=force_type,
        headless=headless,
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Universal Google Drive / Docs / Sheets / Slides / Video / Folder Downloader"
    )
    parser.add_argument("url", help="Google Drive, Docs, Sheets, Slides, Folder URL or File ID")
    parser.add_argument("-o", "--output-dir", default="./downloads", help="Target directory (default: ./downloads)")
    parser.add_argument("-f", "--format", default=None, help="Export format: docx/pdf/txt/md/html (Docs), xlsx/csv (Sheets), pptx/pdf (Slides)")
    parser.add_argument("-n", "--name", default=None, help="Optional output base filename (without extension)")
    parser.add_argument("--type", dest="force_type", choices=["video", "pdf", "gdoc", "gsheet", "gslide", "folder"], default=None, help="Force resource handler")
    parser.add_argument("--authuser", default=None, help="Google account index (0, 1, 2...) when multiple accounts are signed in")
    parser.add_argument("--profile", default=None, help="Explicit Firefox profile folder or cookies.sqlite path")
    parser.add_argument("--cookie-file", default=None, help="Explicit Playwright JSON cookie file (e.g. ff_cookies.json)")
    parser.add_argument("--headless", action="store_true", help="Run Playwright headless (note: visible browser is more reliable for DASH video)")
    args = parser.parse_args()

    cookies = load_cookies(profile_dir=args.profile, cookie_json_file=args.cookie_file)
    print(f"[Auth] Loaded {len(cookies)} Google/YouTube cookies (Firefox/JSON, Edge untouched).", flush=True)

    result = dispatch_download(
        url=args.url,
        output_dir=Path(args.output_dir).resolve(),
        cookies=cookies,
        fmt=args.format,
        name=args.name,
        force_type=args.force_type,
        authuser=args.authuser,
        headless=args.headless,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
