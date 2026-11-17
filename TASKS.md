# 📋 DANH SÁCH NHIỆM VỤ DỰ ÁN (TASKS.md)

## 🔄 Nhiệm Vụ Đang Thực Hiện (In-Progress)
- [ ] **Đẩy dự án lên GitHub**
  - **Thời gian bắt đầu:** 2026-11-17
  - [x] Khởi tạo repo git cục bộ (nhánh `main`), thêm `.gitignore` (loại trừ cookie, token, `downloads/`, `.agent/history/`)
  - [x] Chạy unit test: 11/11 PASS
  - [x] Commit cục bộ
  - [ ] Đăng nhập GitHub (`gh auth login`) — cần người dùng tự thực hiện
  - [ ] Chọn tên repo và chế độ public/private, tạo remote và push
  - **Ghi chú:** README.md và README.en.md được nhắc trong đề xuất chưa tồn tại trong thư mục dự án.

Trạng thái: Đang chờ đăng nhập GitHub và xác nhận tên/chế độ repo để push.

---

## ✅ Nhiệm Vụ Đã Thực Hiện (Completed)
- [x] **Tổng hợp kinh nghiệm từ `G:\My Drive\HocAptis` & xây dựng Skill tải mọi loại tài nguyên Google Drive (`.agents/skills/gdrive-downloader/`)**
  - **Thời gian hoàn thành:** 2026-11-12 18:57
  - **Kết quả:**
    - Khảo sát đầy đủ bài học thực tế từ `G:\My Drive\HocAptis` (`AGENTS.md`, `PROJECT_MAP.md`, `TASKS.md`, lịch sử prompt, các script `extract_ff_cookies.py`, `download_dash_tracks.py`, `download_jane_vocab_video.py`, `fetch_gdoc.py`, `download_gdoc_pw.py`, `extract_all_gdoc_text.py`, `convert_gdoc_to_md.py`, `rebuild_clean_md.py`) và tham chiếu Gemini 4 High (`/g4`).
    - Xây dựng hoàn chỉnh Skill `.agents/skills/gdrive-downloader/` gồm:
      - `SKILL.md`: Hướng dẫn kích hoạt, quy tắc an toàn (không đụng Edge, dùng Firefox + Playwright cô lập), ma trận waterfall và lệnh CLI 1 bước.
      - `references/edge-cases.md`: Tài liệu hóa 10+ kịch bản (Video DASH mất tiếng, `ump`/`srfvp`, Google Docs nhiều tab, lọc JS/CSS `DOCS_initDocsMobileWeb`, Docs canvas `DOCS_modelChunk`, Google Sheets `/gviz` & `/htmlview` `.waffle`, Google Slides `/embed`, cảnh báo virus `confirm=t`/`uuid`, PDF view-only chụp trang, Folder/Shortcut chống lặp, đa tài khoản `?authuser=`, Quota 24h).
      - `scripts/`: `gdrive_common.py`, `gdrive_auth.py`, `gdrive_media.py`, `gdrive_workspace.py`, `gdrive_files.py`, `download_gdrive.py`.
    - Kiểm chứng `quick_validate.py` (`Skill is valid!`), `download_gdrive.py --help` (exit 0) và `tests/test_gdrive_downloader.py` (**11/11 tests PASS**).
  - **Files liên quan:**
    - [SKILL.md](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/SKILL.md)
    - [edge-cases.md](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/references/edge-cases.md)
    - [download_gdrive.py](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/scripts/download_gdrive.py)
    - [gdrive_auth.py](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/scripts/gdrive_auth.py)
    - [gdrive_common.py](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/scripts/gdrive_common.py)
    - [gdrive_media.py](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/scripts/gdrive_media.py)
    - [gdrive_workspace.py](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/scripts/gdrive_workspace.py)
    - [gdrive_files.py](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/.agents/skills/gdrive-downloader/scripts/gdrive_files.py)
    - [test_gdrive_downloader.py](file:///d:/Projects/Anti_G_7986/DownloadGoogleDrive/tests/test_gdrive_downloader.py)
