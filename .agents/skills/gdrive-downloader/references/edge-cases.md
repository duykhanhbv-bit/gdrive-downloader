# Sổ Tay Kinh Nghiệm & Các Trường Hợp Góc Khuất Google Drive

Tài liệu này tổng hợp toàn bộ bài học thực chiến từ dự án `G:\My Drive\HocAptis` và các tình huống ngoại lệ khi tải tài nguyên Google Drive / Google Workspace.

---

## 1. Xác thực & Cookie (Firefox vs Edge)

- **Bài học từ `HocAptis`:** Tuyệt đối không đóng hoặc chiếm dụng trình duyệt Microsoft Edge cá nhân của người dùng. Luôn đọc cookie từ hồ sơ Firefox (`cookies.sqlite`).
- **Firefox đang mở:** Khi Firefox đang chạy, cookie mới nhất có thể nằm trong SQLite WAL (`cookies.sqlite-wal`, `cookies.sqlite-shm`). Script `gdrive_auth.py` tự động copy cả 3 file sang thư mục tạm trước khi mở SQLite để không bị lỗi lock và không sót session mới đăng nhập.
- **Chuẩn hóa `expiry` cho Playwright:** Firefox có thể lưu thời hạn cookie ở dạng giây hoặc mili-giây/micro-giây (`> 10^10`). Phải chia `1000` (hoặc `1_000_000` nếu `> 10^14`) về UNIX seconds trước khi gọi `context.add_cookies()`.
- **Đa tài khoản Google (`?authuser=`):** Khi người dùng đăng nhập nhiều Gmail trên Firefox, link chia sẻ riêng cho tài khoản thứ 2 có thể trả 403/404 ở mặc định `authuser=0`. Truyền `--authuser 1` (hoặc dùng URL `/u/1/`).

---

## 2. Video Google Drive (Kể cả View-Only khóa nút tải)

- **Sai lầm phổ biến:** Chỉ bắt URL `videoplayback` đầu tiên rồi tải về $\rightarrow$ video bị **mất tiếng hoàn toàn** vì Drive phát video dài qua DASH tách riêng **Video Track** (`mime=video`, `itag=137/136/135/134`) và **Audio Track** (`mime=audio`, `itag=140/251`).
- **Bẫy tham số Protobuf:** URL `videoplayback` bắt được từ network chứa các tham số phân đoạn và đóng gói Protobuf (`range`, `rn`, `rbuf`, `ump`, `srfvp`). Nếu giữ nguyên `ump=1` hoặc `srfvp=1`, dữ liệu tải về bị bọc header nhị phân khiến FFmpeg không đọc được. **Bắt buộc xóa sạch 5 tham số này** rồi mới tải tuần tự từng dải `&range=start-end` (5MB/chunk).
- **Cùng ngữ cảnh TLS/Session:** Nên tải chunk ngay bằng `context.request.get(...)` bên trong chính phiên Playwright vừa mở player (hoặc dùng đúng cookie + User-Agent), tránh lỗi `403 Forbidden` do lệch IP/session token.
- **Ghép & kiểm chứng:**
  1. `ffmpeg -y -i video_only.mp4 -i audio_only.m4a -c:v copy -c:a copy final.mp4` (nếu audio là Opus/WebM không tương thích `.mp4`, fallback sang `-c:a aac -b:a 192k`).
  2. Kiểm chứng bắt buộc bằng `ffprobe` (đủ cả stream `video` và `audio`, `duration > 0`) + OpenCV `cv2.VideoCapture` đọc thử khung hình đầu tiên.

---

## 3. Google Docs (Nhiều Tab & Khóa tải/In/Sao chép)

- **Docs nhiều Document Tabs (`?tab=t.xxx`):**
  - Khi tải bằng `/export?format=docx`, Google Docs thường xuất toàn bộ các tab; tuy nhiên nếu chỉ truyền một `tab=t.xxx` hoặc tải dạng `/export?format=txt`, có thể bị thiếu nội dung tab khác.
  - Khi fallback sang `/mobilebasic`, Google Docs liệt kê đầy đủ nội dung các tab trong HTML.
- **Bẫy rò rỉ JS/CSS trong HTML (`HocAptis` ngày 2026-09-27):**
  - Khi parse trang `mobilebasic` hoặc HTML Google Docs, nếu không loại bỏ thẻ `<script>`, `<style>`, `<noscript>` trước khi duyệt cây DOM, mã JavaScript nội bộ (`DOCS_initDocsMobileWeb`, `navigator.sendBeacon`, `enterCoreJsErrorDialog`, `counter-increment`) sẽ bị trộn thẳng vào file `.md` và `.docx`.
  - Giải pháp trong `gdrive_workspace.py`: gọi `.decompose()` trên toàn bộ `script`, `style`, `noscript`, `meta`, `link`, `svg` **trước** khi convert sang Markdown, đồng thời rà soát lại output theo danh sách `BAD_DOC_MARKERS`.
- **Google Docs render bằng `<canvas>`:** Các bản Docs mới render nội dung lên `<canvas>` thay vì DOM text. Khi gặp trường hợp này, script tự bóc `DOCS_modelChunk` trong HTML nguồn hoặc điều khiển Playwright bấm qua từng tab (`[role="tab"]`) và cuộn `.kix-appview-editor` (`PageDown`) để thu thập văn bản.
- **Chuẩn `.docx` tạo từ fallback:** Mọi `.docx` tái tạo từ Markdown đều được tự động chèn `<w:fldSimple w:instr="PAGE"/>` căn giữa ở Footer và bật `different_first_page_header_footer = True`.

---

## 4. Google Sheets (Nhiều Sheet & View-Only khóa tải)

- **Tải chuẩn:** `/export?format=xlsx` giữ nguyên định dạng, công thức và toàn bộ các sheet. Cần kiểm tra 2 byte đầu là `PK` (chuẩn ZIP/XLSX) để chắc chắn không lưu nhầm trang HTML đăng nhập.
- **Khi bị khóa Download/Print/Copy (403):**
  1. Thử endpoint `/gviz/tq?tqx=out:csv&gid={GID}` (nhiều sheet view-only vẫn mở `gviz`).
  2. Thử endpoint `/htmlview` (hoặc mở `/htmlview` qua Playwright): bóc danh sách tên sheet từ `#sheet-menu li` / `items.push(...)` và từng bảng `table.waffle` (bỏ qua cột chỉ số `row-header-shim`, xử lý `colspan`), sau đó dùng `openpyxl` dựng lại file `.xlsx` đa sheet sạch sẽ và mở kiểm chứng lại workbook.

---

## 5. Các Trường Hợp Mở Rộng Khác

1. **File dung lượng lớn (>100MB) bị cảnh báo virus:**
   - Google Drive trả về trang HTML *"Google Drive can't scan this file for viruses"*.
   - Không bao giờ lưu mù luồng HTTP 200 xuống file! Phải sniff 16KB đầu tiên: nếu phát hiện HTML `virus_scan_warning`, dùng `parse_virus_scan_form()` trích `action` và các input ẩn (`id`, `export`, `confirm`, `uuid`, `at`) từ `<form id="download-form">` để gửi request xác nhận tải thật.
2. **File PDF / Ảnh bị khóa nút tải (View-only):**
   - Playwright mở `/view`, cuộn qua toàn bộ các trang (`PageDown`), bắt các response ảnh render `viewerng/img` / `drive-viewer` (hoặc chụp `<canvas>` / `<img>` trong khung tài liệu), loại trùng theo hash và đóng gói lại thành `.pdf` bằng Pillow.
3. **Google Slides bị khóa tải:**
   - Playwright mở `/embed`, tự động nhấn `ArrowRight` qua từng slide, chụp PNG từng trang + gom chữ, đóng gói thành `.pdf` + `_text.md`.
4. **Google Drive Folder & Shortcut:**
   - Playwright mở thư mục, cuộn `End` nhiều lần để nạp hết danh sách virtualized `[data-id]`, nhận diện loại từng item (Folder, Docs, Sheets, Slides, File thường), giữ tập `visited` ID để tránh vòng lặp vô hạn khi gặp Shortcut trỏ chéo thư mục, rồi đệ quy tải xuống toàn bộ cây thư mục.
5. **Quota Exceeded (Giới hạn lượt tải 24h):**
   - Khi gặp thông báo *"Too many users have viewed or downloaded this file recently"*, hệ thống báo rõ nguyên nhân. Cách xử lý nhanh nhất là mở link bằng tài khoản Google của mình $\rightarrow$ chọn **Tạo bản sao (Make a copy)** hoặc **Thêm lối tắt vào Drive**, sau đó tải bản sao đó về.
