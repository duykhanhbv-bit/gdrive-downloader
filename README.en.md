# gdrive-downloader

**English** • [Tiếng Việt](README.md)

A command-line tool that downloads any Google Drive and Google Workspace resource to your machine: regular files, PDFs, DASH videos (video and audio are separate streams), Google Docs, Sheets, Slides, and folders. It also handles view-only files where the Download, Print, and Copy buttons are disabled.

## Features

- **Google Docs**: exports all tabs, strips leaked JS/CSS, and writes `.docx`, `.pdf`, `.md`, `.txt`, or `.html`.
- **Google Sheets**: exports multi-sheet `.xlsx`; when export is blocked, it rebuilds the workbook from `gviz` or `/htmlview`.
- **Google Slides**: exports `.pptx` or `.pdf`; when export is blocked, it captures each slide.
- **Drive videos**: merges the DASH video and audio streams with `ffmpeg`, so the audio is not lost.
- **PDFs and regular files**: handles the virus-scan warning on large files; view-only PDFs are captured page by page.
- **Folders**: recursive download, with protection against shortcut loops.
- **Authentication**: reads cookies from the **Firefox** profile you already use. It never touches Edge or Chrome.

## Installation

Requirements: Python 3, Firefox signed in to your Google account, and these tools on your `PATH`:

- `ffmpeg` and `ffprobe` (merging video, checking files)
- `yt-dlp` (fast path for videos)

Install the Python packages:

```bash
pip install requests beautifulsoup4 playwright openpyxl python-docx pillow opencv-python yt-dlp gdown
python -m playwright install chromium
```

## Quick start

```bash
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "<GOOGLE_DRIVE_LINK>" -o "./downloads"
```

Examples:

```bash
# Download a Google Doc as PDF
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "https://docs.google.com/document/d/<ID>/edit" -f pdf

# Download a Google Sheet with a custom file name
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "https://docs.google.com/spreadsheets/d/<ID>/edit" -f xlsx -n payroll

# Use the second Google account signed in to Firefox
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "<LINK>" --authuser 1
```

## CLI options

| Option | Meaning |
| :--- | :--- |
| `-o, --output-dir` | Output folder (default `./downloads`) |
| `-f, --format` | Export format: `docx`/`pdf`/`md`/`txt`/`html` (Docs), `xlsx`/`csv` (Sheets), `pptx`/`pdf` (Slides) |
| `-n, --name` | Output file name without extension |
| `--type` | Force the handler: `video`, `pdf`, `gdoc`, `gsheet`, `gslide`, `folder` |
| `--authuser` | Google account index (`0`, `1`, `2`...) when Firefox is signed in to several accounts |
| `--profile` | Firefox profile folder or path to `cookies.sqlite` |
| `--cookie-file` | Playwright-style JSON cookie file (for example `ff_cookies.json`) |
| `--headless` | Run Playwright without a visible window (DASH video is more reliable with a visible window) |

## Waterfall fallback matrix

The tool tries the fast path first and moves to the fallback only when the previous step fails.

| Resource | Fast path | Fallback when download is blocked | Output check |
| :--- | :--- | :--- | :--- |
| Drive video | Direct download via `yt-dlp` with cookies | Capture the DASH video and audio streams in parallel with Playwright, download in 5 MB chunks, merge with `ffmpeg -c copy` | `ffprobe` finds both video and audio streams, `duration > 0` |
| Google Docs | Export `docx` (keeps all tabs) | Read `/mobilebasic` with script and style removed; scroll through tabs when content is drawn on canvas | Correct file signature; no leftover internal JS |
| Google Sheets | Export `xlsx` | `gviz` as CSV, then `/htmlview` to extract tables and rebuild `.xlsx` | Reopens with `openpyxl` |
| Google Slides | Export `pptx` or `pdf` | Open `/embed`, capture each slide, collect text into `.pdf` and `_text.md` | Slide count matches and the PDF opens |
| File / PDF | Download via `usercontent`, bypassing the virus-scan warning | Open `/view` and capture each page into `.pdf` | Size `> 0`, not an HTML page |
| Folder | Walk and download each file | Recursive, with a `visited` set to prevent loops | JSON report of total items and status |

For harder cases (expired cookies, the 24-hour quota, Docs rendered on canvas, video without audio), see [references/edge-cases.md](.agents/skills/gdrive-downloader/references/edge-cases.md).

## Why Firefox?

Chromium browsers (Chrome, Edge) since version 127 encrypt cookies with App-Bound Encryption (DPAPI, key version `v20`). Only the running browser can decrypt that key, and the cookie database is usually locked while the browser is open. Firefox stores cookies unencrypted in `cookies.sqlite`. When Firefox is running, the tool copies `cookies.sqlite-wal` and `cookies.sqlite-shm` to a temporary folder before reading, so it picks up the newest session without fighting over the file lock.

## Testing

```bash
python -m unittest discover -s tests -v
```

There are 11 unit tests. They cover URL parsing, cookie expiry normalization, stripping JS/CSS from Docs, rebuilding `.xlsx` from Sheets HTML, and page numbering in `.docx`.

## Project layout

```
.agents/skills/gdrive-downloader/
├── SKILL.md                 # Instructions for the agent and safety rules
├── references/edge-cases.md # Notes on edge cases
└── scripts/                 # Main source code
tests/test_gdrive_downloader.py
```

## Notes

- Use this tool only for files you have legitimate access to. Do not share your cookies or signed-in sessions.
- Do not commit cookie files, tokens, or the `downloads/` folder. `.gitignore` already excludes them.
