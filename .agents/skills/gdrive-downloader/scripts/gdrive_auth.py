# -*- coding: utf-8 -*-
"""Firefox cookie extraction (never touching Edge) for requests, Playwright, and yt-dlp/gdown."""

from __future__ import annotations

import configparser
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def normalize_expiry(expiry) -> Optional[int]:
    """Convert Firefox expiry timestamps (sec/ms/us) to UNIX seconds for Playwright."""
    if not expiry:
        return None
    try:
        val = int(expiry)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    if val > 10**14:
        val //= 1_000_000
    elif val > 10**10:
        val //= 1000
    return val


def _firefox_base_dirs() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Mozilla" / "Firefox")
    home = Path.home()
    candidates.append(home / "AppData" / "Roaming" / "Mozilla" / "Firefox")
    candidates.append(home / ".mozilla" / "firefox")
    candidates.append(home / "Library" / "Application Support" / "Firefox")
    seen: set[Path] = set()
    unique: list[Path] = []
    for c in candidates:
        if c not in seen and c.exists():
            seen.add(c)
            unique.append(c)
    return unique


def find_firefox_profile(explicit_profile: Optional[str] = None) -> Optional[Path]:
    """Auto-discover the active Firefox profile containing cookies.sqlite."""
    if explicit_profile:
        p = Path(explicit_profile)
        if p.is_file() and p.name == "cookies.sqlite":
            return p.parent
        if p.is_dir() and (p / "cookies.sqlite").exists():
            return p

    for base in _firefox_base_dirs():
        ini_path = base / "profiles.ini"
        if ini_path.exists():
            cfg = configparser.RawConfigParser()
            try:
                cfg.read(ini_path, encoding="utf-8")
                profiles: list[tuple[int, Path]] = []
                for sec in cfg.sections():
                    if not sec.startswith("Profile"):
                        continue
                    rel = cfg.get(sec, "IsRelative", fallback="1") == "1"
                    raw_path = cfg.get(sec, "Path", fallback="")
                    if not raw_path:
                        continue
                    prof_dir = (base / raw_path) if rel else Path(raw_path)
                    if not (prof_dir / "cookies.sqlite").exists():
                        continue
                    score = 0
                    if cfg.get(sec, "Default", fallback="0") == "1":
                        score += 10
                    if "default-release" in prof_dir.name:
                        score += 5
                    profiles.append((score, prof_dir))
                if profiles:
                    profiles.sort(key=lambda x: (x[0], (x[1] / "cookies.sqlite").stat().st_mtime), reverse=True)
                    return profiles[0][1]
            except Exception:
                pass

        profiles_dir = base / "Profiles"
        if profiles_dir.exists():
            valid = [
                d for d in profiles_dir.iterdir()
                if d.is_dir() and (d / "cookies.sqlite").exists()
            ]
            if valid:
                valid.sort(
                    key=lambda d: ("default-release" in d.name, (d / "cookies.sqlite").stat().st_mtime),
                    reverse=True,
                )
                return valid[0]
    return None


def load_cookies(
    profile_dir: Optional[str] = None,
    cookie_json_file: Optional[str] = None,
) -> list[dict]:
    """Load Google/YouTube cookies from a JSON file or Firefox's cookies.sqlite (safe temp copy)."""
    if cookie_json_file:
        p = Path(cookie_json_file)
        if not p.exists():
            raise FileNotFoundError(f"Cookie JSON file not found: {p}")
        raw = json.loads(p.read_text(encoding="utf-8"))
        out: list[dict] = []
        for c in raw:
            item = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".google.com"),
                "path": c.get("path", "/"),
                "secure": bool(c.get("secure", False)),
                "httpOnly": bool(c.get("httpOnly", False)),
            }
            exp = normalize_expiry(c.get("expires"))
            if exp:
                item["expires"] = exp
            out.append(item)
        return out

    prof = find_firefox_profile(profile_dir)
    if not prof:
        return []

    src_db = prof / "cookies.sqlite"
    if not src_db.exists():
        return []

    cookies: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="gdrive_ff_") as tmpdir:
        tmp_db = Path(tmpdir) / "cookies.sqlite"
        shutil.copy2(src_db, tmp_db)
        # Copy WAL/SHM if Firefox is actively open so uncommitted session cookies are included
        for suffix in ("-wal", "-shm"):
            side = prof / f"cookies.sqlite{suffix}"
            if side.exists():
                try:
                    shutil.copy2(side, Path(tmpdir) / f"cookies.sqlite{suffix}")
                except OSError:
                    pass

        conn = sqlite3.connect(str(tmp_db))
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT host, name, value, path, expiry, isSecure, isHttpOnly "
                "FROM moz_cookies "
                "WHERE host LIKE '%google%' OR host LIKE '%youtube%'"
            )
            for host, name, value, path, expiry, is_secure, is_httponly in cur.fetchall():
                item = {
                    "name": name,
                    "value": value,
                    "domain": host,
                    "path": path or "/",
                    "secure": bool(is_secure),
                    "httpOnly": bool(is_httponly),
                }
                exp = normalize_expiry(expiry)
                if exp:
                    item["expires"] = exp
                cookies.append(item)
        finally:
            conn.close()
    return cookies


def build_requests_session(cookies: list[dict], user_agent: str = DEFAULT_UA) -> requests.Session:
    """Create a requests.Session preloaded with Google/YouTube cookies."""
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    for c in cookies:
        session.cookies.set(
            c["name"],
            c["value"],
            domain=c["domain"],
            path=c.get("path", "/"),
        )
    return session


def write_netscape_cookies(cookies: list[dict], dest_path: Path) -> Path:
    """Write cookies in Netscape format for yt-dlp / gdown / curl."""
    lines = ["# Netscape HTTP Cookie File", "# Generated by gdrive-downloader"]
    for c in cookies:
        domain = c["domain"]
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = str(c.get("expires", 0))
        name = c["name"]
        value = c["value"]
        lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    dest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest_path
