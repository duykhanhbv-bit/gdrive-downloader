# -*- coding: utf-8 -*-
"""Google Drive video/audio downloader: yt-dlp fast-path + Playwright DASH dual-track fallback."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from gdrive_auth import DEFAULT_UA, write_netscape_cookies
from gdrive_common import add_query_params, ensure_unique_path, sanitize_filename


VIDEO_ITAG_PRIORITY = ["137", "299", "298", "136", "135", "134", "133", "160"]
AUDIO_ITAG_PRIORITY = ["140", "141", "251", "250", "249", "171", "139"]
MUXED_ITAG_PRIORITY = ["22", "18"]
STRIP_PARAMS = ("range", "rn", "rbuf", "ump", "srfvp")


def strip_stream_params(raw_url: str) -> tuple[str, int]:
    """Remove Protobuf/range query params (ump, srfvp, range, rn, rbuf) and extract clen."""
    parsed = urllib.parse.urlparse(raw_url)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    clen_vals = qs.get("clen") or ["0"]
    try:
        clen = int(clen_vals[0])
    except (ValueError, TypeError):
        clen = 0
    for key in STRIP_PARAMS:
        qs.pop(key, None)
    clean_query = urllib.parse.urlencode(qs, doseq=True)
    clean_url = urllib.parse.urlunparse((
        parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, parsed.fragment
    ))
    return clean_url, clen


def classify_stream_url(url: str) -> Optional[tuple[str, int]]:
    """Classify a videoplayback/googlevideo URL as ('video'|'audio'|'muxed', priority_rank)."""
    if "videoplayback" not in url and "googlevideo.com" not in url:
        return None
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    itag = (qs.get("itag") or [""])[0]
    mime = urllib.parse.unquote((qs.get("mime") or [""])[0]).lower()

    if itag in VIDEO_ITAG_PRIORITY:
        return ("video", VIDEO_ITAG_PRIORITY.index(itag))
    if itag in AUDIO_ITAG_PRIORITY:
        return ("audio", AUDIO_ITAG_PRIORITY.index(itag))
    if itag in MUXED_ITAG_PRIORITY:
        return ("muxed", MUXED_ITAG_PRIORITY.index(itag))
    if "video/" in mime or "mime=video" in url:
        return ("video", 99)
    if "audio/" in mime or "mime=audio" in url:
        return ("audio", 99)
    return None


def verify_media(file_path: Path, expect_video: bool = True) -> dict:
    """Verify media file integrity via ffprobe (streams + duration) and OpenCV frame read."""
    info = {
        "verified": False,
        "has_video": False,
        "has_audio": False,
        "duration_sec": 0.0,
        "opencv_ok": False,
        "error": None,
    }
    if not file_path.exists() or file_path.stat().st_size == 0:
        info["error"] = "File missing or empty"
        return info

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            res = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-show_entries", "stream=codec_type,width,height",
                    "-show_entries", "format=duration",
                    "-of", "json", str(file_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if res.returncode == 0 and res.stdout.strip():
                meta = json.loads(res.stdout)
                streams = meta.get("streams", [])
                info["has_video"] = any(s.get("codec_type") == "video" for s in streams)
                info["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
                dur = meta.get("format", {}).get("duration")
                if dur:
                    info["duration_sec"] = round(float(dur), 2)
        except Exception as exc:
            info["error"] = f"ffprobe error: {exc}"

    if expect_video and info["has_video"]:
        try:
            import cv2  # type: ignore
            cap = cv2.VideoCapture(str(file_path))
            if cap.isOpened():
                ret, _ = cap.read()
                info["opencv_ok"] = bool(ret)
            cap.release()
        except ImportError:
            info["opencv_ok"] = True  # OpenCV optional if ffprobe already passed
        except Exception as exc:
            info["error"] = f"OpenCV error: {exc}"
    elif not expect_video:
        info["opencv_ok"] = True

    if info["duration_sec"] > 0 and (not expect_video or (info["has_video"] and info["opencv_ok"])):
        info["verified"] = True
    elif not ffprobe and file_path.stat().st_size > 1024:
        info["verified"] = True

    return info


def try_ytdlp_download(
    url: str,
    output_dir: Path,
    base_name: Optional[str],
    cookies: list[dict],
) -> Optional[Path]:
    """Fast-path video download using yt-dlp with Firefox Netscape cookies."""
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        return None

    with tempfile.TemporaryDirectory(prefix="gdrive_ytdlp_") as tmpdir:
        tmp_path = Path(tmpdir)
        cookie_txt = tmp_path / "cookies.txt"
        write_netscape_cookies(cookies, cookie_txt)

        out_tpl = str(tmp_path / (f"{base_name}.%(ext)s" if base_name else "%(title).120s.%(ext)s"))
        cmd = [
            ytdlp,
            "--no-playlist",
            "--cookies", str(cookie_txt),
            "--user-agent", DEFAULT_UA,
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", out_tpl,
            url,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return None

        if res.returncode != 0:
            return None

        files = [f for f in tmp_path.iterdir() if f.is_file() and f.name != "cookies.txt"]
        if not files:
            return None
        produced = max(files, key=lambda p: p.stat().st_size)
        vinfo = verify_media(produced, expect_video=True)
        if not vinfo["verified"] or not vinfo["has_audio"]:
            return None

        output_dir.mkdir(parents=True, exist_ok=True)
        dest = ensure_unique_path(output_dir / f"{sanitize_filename(produced.stem)}{produced.suffix}")
        shutil.move(str(produced), str(dest))
        return dest


def _probe_total_bytes(req_context, clean_url: str) -> int:
    """Fallback probe via Range: bytes=0-0 if clen query param is missing."""
    try:
        sep = "&" if "?" in clean_url else "?"
        r = req_context.get(f"{clean_url}{sep}range=0-0", timeout=30000)
        cr = r.headers.get("content-range") or r.headers.get("Content-Range") or ""
        m = re.search(r"/(\d+)", cr)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


def _download_dash_track(req_context, raw_url: str, out_file: Path, label: str) -> None:
    """Download a single DASH stream sequentially in 5MB chunks inside the Playwright session."""
    clean_url, clen = strip_stream_params(raw_url)
    if clen <= 0:
        clen = _probe_total_bytes(req_context, clean_url)

    sep = "&" if "?" in clean_url else "?"
    if clen <= 0:
        r = req_context.get(clean_url, timeout=120000)
        if r.status not in (200, 206):
            raise RuntimeError(f"{label} download failed with status {r.status}")
        out_file.write_bytes(r.body())
        return

    chunk_size = 5 * 1024 * 1024
    downloaded = 0
    with open(out_file, "wb") as f:
        while downloaded < clen:
            end = min(downloaded + chunk_size - 1, clen - 1)
            chunk_url = f"{clean_url}{sep}range={downloaded}-{end}"
            last_err: Optional[Exception] = None
            for attempt in range(5):
                try:
                    r = req_context.get(chunk_url, timeout=60000)
                    if r.status in (200, 206):
                        body = r.body()
                        if not body:
                            raise RuntimeError(f"Empty body for {label} range {downloaded}-{end}")
                        f.write(body)
                        downloaded += len(body)
                        pct = (downloaded / clen) * 100.0
                        print(f"[{label}] {downloaded}/{clen} bytes ({pct:.1f}%)", flush=True)
                        last_err = None
                        break
                    last_err = RuntimeError(f"HTTP {r.status}")
                except Exception as exc:
                    last_err = exc
                time.sleep(min(2 * (attempt + 1), 8))
            if last_err is not None:
                raise RuntimeError(f"{label} chunk failed after 5 retries: {last_err}")


def _mux_av(video_path: Path, audio_path: Path, final_path: Path) -> None:
    """Mux video + audio with FFmpeg direct copy, falling back to AAC audio transcode if container rejects codec."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to merge DASH video and audio tracks.")
    cmd_copy = [
        ffmpeg, "-y", "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "copy", str(final_path),
    ]
    res = subprocess.run(cmd_copy, capture_output=True, text=True)
    if res.returncode == 0 and final_path.exists() and final_path.stat().st_size > 0:
        return

    cmd_aac = [
        ffmpeg, "-y", "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(final_path),
    ]
    res2 = subprocess.run(cmd_aac, capture_output=True, text=True)
    if res2.returncode != 0:
        raise RuntimeError(f"FFmpeg merge failed: {res2.stderr[-600:]}")


def download_drive_video_playwright(
    file_id: str,
    output_dir: Path,
    cookies: list[dict],
    base_name: Optional[str] = None,
    resourcekey: Optional[str] = None,
    authuser: Optional[str] = None,
    headless: bool = False,
    wait_seconds: int = 40,
) -> dict:
    """Capture dual DASH Video+Audio streams in isolated Playwright Chromium (never Edge) and mux with FFmpeg."""
    from playwright.sync_api import sync_playwright  # type: ignore

    view_url = add_query_params(
        f"https://drive.google.com/file/d/{file_id}/view",
        resourcekey=resourcekey,
        authuser=authuser,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    best_video: tuple[int, str] | None = None
    best_audio: tuple[int, str] | None = None
    best_muxed: tuple[int, str] | None = None
    page_title = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=DEFAULT_UA,
            viewport={"width": 1280, "height": 720},
        )
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()

        def on_traffic(url: str) -> None:
            nonlocal best_video, best_audio, best_muxed
            cls = classify_stream_url(url)
            if not cls:
                return
            kind, rank = cls
            if kind == "video" and (best_video is None or rank < best_video[0]):
                best_video = (rank, url)
                print(f"[DASH] Captured Video stream (rank={rank})", flush=True)
            elif kind == "audio" and (best_audio is None or rank < best_audio[0]):
                best_audio = (rank, url)
                print(f"[DASH] Captured Audio stream (rank={rank})", flush=True)
            elif kind == "muxed" and (best_muxed is None or rank < best_muxed[0]):
                best_muxed = (rank, url)
                print(f"[DASH] Captured Muxed stream (rank={rank})", flush=True)

        page.on("request", lambda req: on_traffic(req.url))
        page.on("response", lambda resp: on_traffic(resp.url))

        print(f"[Playwright] Opening Drive viewer: {view_url}", flush=True)
        page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        try:
            page_title = page.title() or ""
        except Exception:
            page_title = ""

        # Trigger player across main page and embedded player iframes
        start = time.time()
        first_hit_at: Optional[float] = None
        while (time.time() - start) < wait_seconds:
            if best_video and best_audio:
                # Give 2s grace window to see if a higher-resolution video itag arrives
                if first_hit_at is None:
                    first_hit_at = time.time()
                elif (time.time() - first_hit_at) >= 2.0:
                    break
            elif best_muxed and first_hit_at is not None and (time.time() - first_hit_at) >= 5.0:
                break

            if (best_video or best_audio or best_muxed) and first_hit_at is None:
                first_hit_at = time.time()

            for frame in page.frames:
                for sel in ("div.xWSG7", "video", "[aria-label*='Play']", "[aria-label*='Phát']"):
                    try:
                        frame.click(sel, force=True, timeout=500)
                    except Exception:
                        pass
            try:
                page.mouse.click(640, 360)
                page.keyboard.press("Space")
            except Exception:
                pass
            time.sleep(1.0)

        if not (best_video or best_audio or best_muxed):
            browser.close()
            raise RuntimeError(
                "Could not intercept videoplayback/googlevideo DASH streams. "
                "Ensure Firefox is logged into a Google account with view access."
            )

        stem = sanitize_filename(base_name or page_title or f"drive_video_{file_id}")
        final_path = ensure_unique_path(output_dir / f"{stem}.mp4")

        with tempfile.TemporaryDirectory(prefix="gdrive_dash_") as tmpdir:
            tmp_dir = Path(tmpdir)
            req_ctx = context.request
            if best_video and best_audio:
                v_tmp = tmp_dir / "video_only.mp4"
                a_tmp = tmp_dir / "audio_only.m4a"
                _download_dash_track(req_ctx, best_video[1], v_tmp, "Video")
                _download_dash_track(req_ctx, best_audio[1], a_tmp, "Audio")
                browser.close()
                _mux_av(v_tmp, a_tmp, final_path)
            elif best_muxed:
                m_tmp = tmp_dir / "muxed.mp4"
                _download_dash_track(req_ctx, best_muxed[1], m_tmp, "Muxed")
                browser.close()
                shutil.move(str(m_tmp), str(final_path))
            elif best_video:
                v_tmp = tmp_dir / "video_only.mp4"
                _download_dash_track(req_ctx, best_video[1], v_tmp, "Video-Only")
                browser.close()
                shutil.move(str(v_tmp), str(final_path))
            else:
                assert best_audio is not None
                final_path = ensure_unique_path(output_dir / f"{stem}.m4a")
                a_tmp = tmp_dir / "audio_only.m4a"
                _download_dash_track(req_ctx, best_audio[1], a_tmp, "Audio-Only")
                browser.close()
                shutil.move(str(a_tmp), str(final_path))

    expect_video = final_path.suffix.lower() == ".mp4"
    vinfo = verify_media(final_path, expect_video=expect_video)
    return {
        "status": "ok" if vinfo["verified"] else "warning",
        "path": str(final_path),
        "size_bytes": final_path.stat().st_size,
        "method": "playwright_dash_mux" if (best_video and best_audio) else "playwright_dash_single",
        "verification": vinfo,
    }
