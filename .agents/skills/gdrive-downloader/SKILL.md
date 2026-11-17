---
name: gdrive-downloader
description: >-
  Tải mọi loại tài nguyên từ Google Drive, Google Docs, Google Sheets, Google Slides, Video, PDF và Thư mục về máy, bao gồm cả trường hợp bị hạn chế chỉ cho xem (view-only, khóa nút Tải/In/Sao chép), video phân luồng DASH tách tiếng/hình, Docs nhiều tab và file lớn cảnh báo virus. Kích hoạt bất cứ khi nào người dùng gửi link drive.google.com hoặc docs.google.com và yêu cầu tải về.
---

# Google Drive Universal Downloader (`gdrive-downloader`)

Skill thống nhất giúp tải về và xác minh mọi liên kết Google Drive / Google Workspace chỉ bằng một lệnh, áp dụng toàn bộ kinh nghiệm thực chiến từ `HocAptis` cùng các cơ chế fallback tự động cho trường hợp bị khóa tải.

## Quy tắc an toàn bắt buộc

1. **Tuyệt đối không đụng trình duyệt Microsoft Edge cá nhân** của người dùng.
2. **Xác thực bằng Firefox + Playwright cô lập**: Tự động đọc `cookies.sqlite` (kèm `-wal`/`-shm` qua bản sao tạm) từ Firefox profile đang hoạt động, chuẩn hóa `expires`, tiêm vào `requests` / `yt-dlp` / Playwright Chromium độc lập. Không in hay commit giá trị cookie ra ngoài.
3. **Không ghi đè file có sẵn**: Tự động tạo hậu tố `_1`, `_2` nếu trùng tên.
4. **File `.docx` tạo từ fallback** phải đánh số trang tự động (`PAGE` căn giữa footer, ẩn trang đầu).

## Thực thi nhanh (Chỉ cần link)

Chạy script điều phối trung tâm tại [download_gdrive.py](./scripts/download_gdrive.py):

```bash
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "<LINK_GOOGLE_DRIVE>" -o "./downloads"
```

### Tùy chọn thường dùng

- `-o, --output-dir`: Thư mục lưu kết quả (mặc định `./downloads`).
- `-f, --format`: Ép định dạng xuất (`docx`/`pdf`/`md`/`txt`/`html` cho Docs; `xlsx`/`csv` cho Sheets; `pptx`/`pdf` cho Slides).
- `-n, --name`: Đặt tên file đầu ra (không cần đuôi mở rộng).
- `--type`: Ép loại xử lý khi link `/file/d/.../view` không tự lộ định dạng (`video`, `pdf`, `gdoc`, `gsheet`, `gslide`, `folder`).
- `--authuser`: Chỉ định chỉ số tài khoản Google (`0`, `1`, `2`...) nếu Firefox đăng nhập nhiều tài khoản.
- `--profile` / `--cookie-file`: Chỉ định thư mục Firefox profile hoặc file JSON cookie riêng.
- `--headless`: Chạy Playwright ẩn cửa sổ (mặc định mở cửa sổ cho video/PDF view-only vì player Google Drive ổn định hơn khi visible).

## Ma trận xử lý tự động (Waterfall Fallback)

| Loại tài nguyên | Luồng nhanh (Tier 1–2) | Fallback khóa tải / chỉ cho View (Tier 3) | Xác minh đầu ra |
| :--- | :--- | :--- | :--- |
| **Video Drive** | `usercontent` raw file → `yt-dlp` kèm cookie Firefox | Playwright bắt song song 2 luồng DASH (`videoplayback` Video + Audio), xóa `ump`/`srfvp`/`range`, tải chunk 5MB, ghép `ffmpeg -c:v copy -c:a copy` | `ffprobe` (đủ stream hình + tiếng, `duration > 0`) + OpenCV đọc frame |
| **Google Docs** | `/export?format=docx` (giữ đủ tab) → Playwright in-page `fetch()` | `/mobilebasic` (xóa `<script>`/`<style>` trước khi parse) → `DOCS_modelChunk` / Playwright cuộn từng tab → `.md` + `.docx` | Kiểm tra magic bytes `PK`/`%PDF-`, không còn chuỗi JS `DOCS_initDocsMobileWeb` |
| **Google Sheets** | `/export?format=xlsx` (hoặc `csv&gid=`) → Playwright `fetch()` | `/gviz/tq?tqx=out:csv` → `/htmlview` bóc toàn bộ tab `.waffle` + `openpyxl` dựng lại `.xlsx` | Mở lại bằng `openpyxl.load_workbook(..., read_only=True)` |
| **Google Slides** | `/export/pptx` (hoặc `pdf`) → Playwright `fetch()` | Mở `/embed` bằng Playwright lật từng slide chụp PNG + gom chữ → `.pdf` + `_text.md` | Kiểm tra số slide chụp & file PDF mở được |
| **File / PDF** | `drive.usercontent.google.com` + parse form cảnh báo virus `confirm=t`/`uuid` → `gdown` | Nếu PDF khóa tải: Playwright mở `/view`, cuộn trang bắt `viewerng/img` / `<canvas>` → `.pdf` | Kiểm tra dung lượng `> 0`, không phải trang HTML lỗi/login |
| **Folder** | Playwright mở `/drive/folders/{id}`, cuộn nạp toàn bộ `[data-id]` | Đệ quy từng thư mục con (chống vòng lặp shortcut bằng `visited`), tự định tuyến từng file | Báo cáo JSON tổng số item & trạng thái từng file |

## Tài liệu tham khảo chuyên sâu

Khi gặp lỗi đặc thù (cookie hết hạn, Quota 24h, đa tài khoản, Docs render bằng canvas, video bị mất tiếng), đọc thêm [references/edge-cases.md](./references/edge-cases.md).
