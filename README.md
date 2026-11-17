# gdrive-downloader

[English](README.en.md) • **Tiếng Việt**

![Python 3](https://img.shields.io/badge/Python-3-blue?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green)
![Playwright](https://img.shields.io/badge/Playwright-automation-45ba4b?logo=playwright&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

Công cụ dòng lệnh tải mọi loại tài nguyên Google Drive và Google Workspace về máy: file thường, PDF, video DASH (tách hình và tiếng), Google Docs, Sheets, Slides và thư mục. Công cụ vẫn tải được các file bị khóa chỉ cho xem (view-only), khóa nút Tải/In/Sao chép.

## Tính năng

- **Google Docs**: xuất đủ các tab, lọc JS/CSS rác, xuất `.docx`, `.pdf`, `.md`, `.txt`, `.html`.
- **Google Sheets**: xuất `.xlsx` đa sheet; nếu bị khóa thì dựng lại từ `gviz` hoặc `/htmlview`.
- **Google Slides**: xuất `.pptx` hoặc `.pdf`; nếu bị khóa thì chụp từng slide.
- **Video Drive**: ghép luồng hình và tiếng DASH bằng `ffmpeg`, không bị mất tiếng.
- **PDF / file thường**: xử lý cảnh báo quét virus cho file lớn; PDF view-only được chụp từng trang.
- **Thư mục**: tải đệ quy, chống vòng lặp do shortcut.
- **Xác thực**: đọc cookie từ hồ sơ **Firefox** đang dùng. Không đụng tới trình duyệt Edge hay Chrome của bạn.

## Cài đặt

Yêu cầu: Python 3, Firefox đã đăng nhập tài khoản Google, và các công cụ sau có trong `PATH`. Code tự dò hồ sơ Firefox trên Windows, macOS và Linux, nhưng mới được kiểm thử thực tế trên Windows.

- `ffmpeg` và `ffprobe` (ghép video, kiểm tra file)
- `yt-dlp` (đường tải nhanh cho video)

Cài thư viện Python:

```bash
pip install requests beautifulsoup4 playwright openpyxl python-docx pillow opencv-python yt-dlp gdown
python -m playwright install chromium
```

## Cách dùng nhanh

```bash
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "<LINK_GOOGLE_DRIVE>" -o "./downloads"
```

Ví dụ:

```bash
# Tải một Google Docs và xuất thành PDF
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "https://docs.google.com/document/d/<ID>/edit" -f pdf

# Tải Google Sheets, đặt tên file
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "https://docs.google.com/spreadsheets/d/<ID>/edit" -f xlsx -n bang_luong

# Tài khoản Google thứ hai trong Firefox
python .agents/skills/gdrive-downloader/scripts/download_gdrive.py "<LINK>" --authuser 1
```

## Tùy chọn dòng lệnh

| Tùy chọn | Ý nghĩa |
| :--- | :--- |
| `-o, --output-dir` | Thư mục lưu kết quả (mặc định `./downloads`) |
| `-f, --format` | Định dạng xuất: `docx`/`pdf`/`md`/`txt`/`html` (Docs), `xlsx`/`csv` (Sheets), `pptx`/`pdf` (Slides) |
| `-n, --name` | Tên file đầu ra, không kèm đuôi |
| `--type` | Ép loại xử lý: `video`, `pdf`, `gdoc`, `gsheet`, `gslide`, `folder` |
| `--authuser` | Chỉ số tài khoản Google (`0`, `1`, `2`...) khi Firefox đăng nhập nhiều tài khoản |
| `--profile` | Thư mục Firefox profile hoặc đường dẫn `cookies.sqlite` |
| `--cookie-file` | File cookie JSON kiểu Playwright (ví dụ `ff_cookies.json`) |
| `--headless` | Chạy Playwright ẩn cửa sổ (video DASH ổn định hơn khi hiện cửa sổ) |

## Ma trận xử lý (Waterfall)

Công cụ thử lần lượt từ luồng nhanh đến luồng dự phòng. Luồng dự phòng chỉ chạy khi luồng trước thất bại.

| Loại tài nguyên | Luồng nhanh | Dự phòng khi bị khóa tải | Kiểm tra đầu ra |
| :--- | :--- | :--- | :--- |
| Video Drive | Tải trực tiếp qua `yt-dlp` kèm cookie | Bắt song song luồng DASH hình và tiếng bằng Playwright, tải theo chunk 5 MB, ghép bằng `ffmpeg -c copy` | `ffprobe` có cả stream hình và tiếng, `duration > 0` |
| Google Docs | Xuất `docx` (giữ đủ tab) | Đọc `/mobilebasic` đã lọc script/style; quét từng tab khi nội dung nằm trong canvas | Đúng magic bytes; không còn mã JS nội bộ |
| Google Sheets | Xuất `xlsx` | `gviz` (CSV), rồi `/htmlview` bóc bảng và dựng lại `.xlsx` | Mở lại được bằng `openpyxl` |
| Google Slides | Xuất `pptx` hoặc `pdf` | Mở `/embed`, chụp từng slide, gom chữ thành `.pdf` và `_text.md` | Số slide và file PDF mở được |
| File / PDF | Tải qua `usercontent`, vượt cảnh báo quét virus | Mở `/view`, chụp từng trang thành `.pdf` | Dung lượng `> 0`, không phải trang HTML |
| Thư mục | Duyệt và tải từng file | Đệ quy, dùng tập `visited` để chống vòng lặp | Báo cáo JSON tổng số file và trạng thái |

Chi tiết các trường hợp khó (cookie hết hạn, giới hạn 24 giờ, Docs render bằng canvas, video mất tiếng): xem [references/edge-cases.md](.agents/skills/gdrive-downloader/references/edge-cases.md).

## Vì sao dùng Firefox?

Chromium (Chrome, Edge) từ phiên bản 127 mã hóa cookie bằng App-Bound Encryption (DPAPI, khóa `v20`). Khóa này chỉ đọc được bởi chính trình duyệt đang chạy, và file cookie thường bị khóa khi trình duyệt mở. Firefox lưu cookie trong `cookies.sqlite` không mã hóa. Khi Firefox đang chạy, công cụ sao chép cả `cookies.sqlite-wal` và `cookies.sqlite-shm` sang thư mục tạm trước khi đọc, nên vẫn lấy được phiên đăng nhập mới nhất mà không tranh khóa file.

## Kiểm thử

```bash
python -m unittest discover -s tests -v
```

Hiện có 11 unit test, bao gồm phân tích URL, chuẩn hóa thời hạn cookie, lọc JS/CSS của Docs, dựng `.xlsx` từ HTML Sheets, và đánh số trang cho `.docx`.

## Cấu trúc thư mục

```
.agents/skills/gdrive-downloader/
├── SKILL.md                 # Hướng dẫn cho agent và quy tắc an toàn
├── references/edge-cases.md # Sổ tay các trường hợp góc khuất
└── scripts/                 # Mã nguồn chính
tests/test_gdrive_downloader.py
```

## Lưu ý

- Công cụ chỉ dùng cho file bạn có quyền truy cập hợp pháp. Không chia sẻ cookie hay phiên đăng nhập của bạn.
- Không commit file cookie, token hay thư mục `downloads/`. `.gitignore` đã loại trừ các mục này.
