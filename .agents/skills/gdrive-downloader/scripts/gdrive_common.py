# -*- coding: utf-8 -*-
"""Common URL parsing, filename sanitization, and response classification helpers."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class ParsedDriveURL:
    raw_url: str
    kind: str  # "gdoc" | "gsheet" | "gslide" | "folder" | "drive_file" | "unknown"
    file_id: str
    resourcekey: Optional[str] = None
    authuser: Optional[str] = None
    tab: Optional[str] = None
    gid: Optional[str] = None


def parse_drive_url(url: str) -> ParsedDriveURL:
    """Parse any Google Drive / Docs / Sheets / Slides URL or raw ID."""
    url = url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", url):
        return ParsedDriveURL(raw_url=url, kind="drive_file", file_id=url)

    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    frag_qs = urllib.parse.parse_qs(parsed.fragment)

    resourcekey = (qs.get("resourcekey") or [None])[0]
    authuser = (qs.get("authuser") or [None])[0]
    if authuser is None:
        u_match = re.search(r"/u/(\d+)/", parsed.path)
        if u_match:
            authuser = u_match.group(1)

    tab = (qs.get("tab") or [None])[0]
    gid = (qs.get("gid") or frag_qs.get("gid") or [None])[0]

    path = parsed.path

    # Google Docs
    m = re.search(r"/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)", path)
    if m:
        return ParsedDriveURL(
            raw_url=url, kind="gdoc", file_id=m.group(1),
            resourcekey=resourcekey, authuser=authuser, tab=tab,
        )

    # Google Sheets
    m = re.search(r"/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9_-]+)", path)
    if m:
        return ParsedDriveURL(
            raw_url=url, kind="gsheet", file_id=m.group(1),
            resourcekey=resourcekey, authuser=authuser, gid=gid,
        )

    # Google Slides
    m = re.search(r"/presentation/(?:u/\d+/)?d/([A-Za-z0-9_-]+)", path)
    if m:
        return ParsedDriveURL(
            raw_url=url, kind="gslide", file_id=m.group(1),
            resourcekey=resourcekey, authuser=authuser,
        )

    # Google Drive Folder
    m = re.search(r"/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)", path)
    if m:
        return ParsedDriveURL(
            raw_url=url, kind="folder", file_id=m.group(1),
            resourcekey=resourcekey, authuser=authuser,
        )

    # Google Drive File (/file/d/<id>, /file/u/0/d/<id>)
    m = re.search(r"/file/(?:u/\d+/)?d/([A-Za-z0-9_-]+)", path)
    if m:
        return ParsedDriveURL(
            raw_url=url, kind="drive_file", file_id=m.group(1),
            resourcekey=resourcekey, authuser=authuser,
        )

    # Query-based id= (open?id=, uc?id=, download?id=)
    if "id" in qs and qs["id"]:
        return ParsedDriveURL(
            raw_url=url, kind="drive_file", file_id=qs["id"][0],
            resourcekey=resourcekey, authuser=authuser,
        )

    return ParsedDriveURL(raw_url=url, kind="unknown", file_id="")


def add_query_params(
    url: str,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    extra: Optional[dict[str, str]] = None,
) -> str:
    """Safely append query parameters without clobbering existing ones."""
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if resourcekey and "resourcekey" not in qs:
        qs["resourcekey"] = [resourcekey]
    if authuser is not None and "authuser" not in qs:
        qs["authuser"] = [str(authuser)]
    if extra:
        for k, v in extra.items():
            if v is not None:
                qs[k] = [str(v)]
    new_query = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse((
        parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment
    ))


def sanitize_filename(name: str, fallback: str = "gdrive_download", max_len: int = 140) -> str:
    """Clean a file/folder name for safe Windows and POSIX paths."""
    cleaned = INVALID_WIN_CHARS.sub("_", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    for suffix in (
        " - Google Drive",
        " - Google Tài liệu",
        " - Google Docs",
        " - Google Trang tính",
        " - Google Sheets",
        " - Google Trang trình bày",
        " - Google Slides",
    ):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip(" .")
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned


def extract_filename_from_cd(content_disposition: str) -> Optional[str]:
    """Parse filename or filename* from Content-Disposition header."""
    if not content_disposition:
        return None
    m_star = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;]+)", content_disposition)
    if m_star:
        return urllib.parse.unquote(m_star.group(1)).strip(' "\'')
    m_plain = re.search(r'filename\s*=\s*"([^"]+)"', content_disposition)
    if m_plain:
        return m_plain.group(1).strip()
    m_bare = re.search(r"filename\s*=\s*([^;\s]+)", content_disposition)
    if m_bare:
        return m_bare.group(1).strip(' "\'')
    return None


def classify_html_block(data: bytes, content_type: str = "") -> Optional[str]:
    """Detect if an HTTP response is an HTML login/block/warning page instead of the target file."""
    ct_lower = (content_type or "").lower()
    head = data[:16384].decode("utf-8", errors="ignore").lower()
    looks_html = "text/html" in ct_lower or "<!doctype html" in head or "<html" in head
    if not looks_html:
        return None

    if "accounts.google.com" in head or "servicelogin" in head or "sign in - google" in head:
        return "login_required"
    if "download_warning" in head or "can't scan this file for viruses" in head or "cannot scan this file for viruses" in head or 'id="download-form"' in head:
        return "virus_scan_warning"
    if "quota exceeded" in head or "too many users have viewed or downloaded" in head:
        return "quota_exceeded"
    if "you need access" in head or "request access" in head or "access denied" in head:
        return "permission_denied"
    return "html_unexpected"


def ensure_unique_path(path: Path) -> Path:
    """Return path if free, or append _1, _2... to avoid overwriting existing user files."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    idx = 1
    while True:
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1
