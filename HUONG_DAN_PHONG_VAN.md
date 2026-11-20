# Hướng Dẫn Vận Hành & Ôn Tập Phỏng Vấn — Hệ Thống Kiểm Tra Lỗi Bề Mặt Kim Loại

## 1. Cách Chạy Nhanh
- **Cách 1 (Khuyên dùng trên Windows):** Nháy đúp tệp `CHAY_DU_AN.bat`. Script tự chuyển về thư mục dự án, chạy `python sample_generator.py` tạo 5 ảnh mẫu trong `samples/`, mở trình duyệt tại `http://127.0.0.1:8050` sau 2 giây và khởi động máy chủ FastAPI.
- **Cách 2 (Terminal thủ công):**
  ```bat
  python sample_generator.py
  python -m uvicorn app:app --host 127.0.0.1 --port 8050
  ```

## 2. Kiến Trúc 3 Tầng
1. **Tầng Giao Diện (`static/index.html` — Frontend SPA thuần, không CDN):** Giao tiếp qua API cùng nguồn (`/api/samples`, `/api/inspect`, `/api/history`), nhận CSP nonce động mỗi lượt tải trang.
2. **Tầng Ứng Dụng & Lưu Trữ (`app.py` — FastAPI + SQLite WAL):**
   - Kiểm soát định dạng, magic-bytes (Pillow `verify()` + `load()`), giới hạn 10 MB và 25 triệu điểm ảnh giải mã hoàn toàn trên RAM (không lưu tệp tải lên xuống đĩa).
   - Giới hạn tần suất `120 req / 60s / IP` trên `/api/*`, gắn bộ security headers (`CSP`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `no-store`).
   - Lưu lịch sử vào `history.db` (WAL mode, index `idx_inspections_id_desc`, 100% truy vấn tham số hóa `?`).
3. **Tầng Thị Giác Máy Tính (`vision_engine.py` — OpenCV + NumPy):** Xử lý ảnh chuẩn `600x400`, trích xuất lỗi trên ROI `[44..556, 36..364]`, phân loại và trả về 3 ảnh Base64 PNG (`original_image`, `processed_mask`, `annotated_image`).

## 3. Quy Trình 4 Bước OpenCV Trong `vision_engine.py`
1. **Tiền xử lý (Resize & Gaussian Blur):** Chuẩn hóa ảnh về `600x400` (`INTER_AREA`), chuyển mức xám và làm mượt bằng `GaussianBlur((5, 5), 0)` để triệt nhiễu vân phay siêu mịn (`±3`) mà vẫn giữ biên lỗi thật.
2. **Tách ROI & Kết hợp Hình thái học + Nền cục bộ:** Cắt vùng kiểm tra ROI `blur[36:364, 44:556]`. Tính `max(BLACKHAT, TOPHAT)` với kernel elip `(25, 25)` để bắt cả lỗi tối lẫn dị vật sáng, kết hợp `absdiff` với nền cục bộ `GaussianBlur((151, 151), BORDER_REPLICATE)` nhằm bù vùng tối/sáng lớn như mẻ góc.
3. **Nhị phân hóa & Đóng lỗ (Threshold & Morphology Close):** Lấy ngưỡng `26`, sau đó áp dụng `MORPH_CLOSE` với kernel `(7, 7)` để nối liền các đoạn vết xước bị đứt quãng và làm đặc vùng khuyết tật.
4. **Tìm đường bao & Phân loại theo luật (FindContours & Rule-based):** Tìm `RETR_EXTERNAL`, lọc vùng nhiễu có diện tích `< 120 px`. Dựa vào cờ chạm biên ROI (`touches_edge`) và tỷ lệ khung hình (`aspect = max(w,h)/min(w,h)`):
   - `aspect >= 3.2` và không chạm biên $\rightarrow$ **Vết xước bề mặt (`Scratch`)**.
   - `area >= 900` hoặc chạm biên $\rightarrow$ **Nứt vỡ / Biến dạng (`Crack`)**.
   - Còn lại $\rightarrow$ **Vết ố / Dị vật (`Stain`)**.

## 4. Bộ 5 Câu Hỏi Phỏng Vấn Thường Gặp
1. **Vì sao dùng kết hợp Black-Hat, Top-Hat và Local Background Diff thay vì ngưỡng toàn cục (Global Threshold)?**
   - *Gợi ý:* Bề mặt kim loại thực tế có ánh sáng không đều (gradient `±10` hoặc `±16`). Ngưỡng cố định dễ báo giả ở vùng tối. `Black-Hat` làm nổi bật vết tối mảnh (xước, nứt) trên nền sáng, `Top-Hat` bắt điểm sáng bất thường, còn `absdiff` với nền mờ `(151, 151)` phát hiện tốt vùng khuyết tật diện tích rộng như mẻ góc mà không phụ thuộc độ sáng chung của lô hàng.
2. **Hệ thống xử lý thay đổi ánh sáng giữa các lô sản phẩm như thế nào?**
   - *Gợi ý:* Nhờ dùng phép toán tương phản cục bộ (`morph_diff` với vùng lân cận 25px và `local_bg` 151px) thay vì so sánh cường độ tuyệt đối, mẫu lô 1 nền `(192,195,200)` và mẫu lô 2 tối hơn `(170,176,184)` kèm gradient chéo `±16` đều cho kết quả `PASS` với 0 báo giả.
3. **Làm thế nào hệ thống phòng chống lỗ hổng Upload, XSS và SQL Injection?**
   - *Gợi ý:* Upload chỉ chấp nhận đuôi trong allow-list (`.png/.jpg/.jpeg/.webp/.bmp`), giới hạn `10 MB`, `MAX_IMAGE_PIXELS = 25M` chống decompression bomb, kiểm tra magic bytes & cấu trúc ảnh bằng Pillow trên RAM và không ghi ra ổ đĩa. Chống XSS bằng `CSP` dùng nonce động sinh từ `secrets.token_urlsafe(16)` trên `GET /`. Chống SQLi bằng 100% câu lệnh SQLite tham số hóa `?` và không trả traceback ra client.
4. **Vì sao phải cắt vùng ROI `[44..556, 36..364]` trước khi dò lỗi?**
   - *Gợi ý:* Bên ngoài tấm kim loại là khay đỡ tối màu `(38,42,48)` và viền vát `2px` `(128,134,142)`. Nếu chạy dò biên trên toàn ảnh, sự chênh lệch lớn giữa khay và mép vát sẽ bị nhận nhầm là khuyết tật. ROI cô lập vùng bề mặt cần kiểm định, còn lỗi chạm sát mép ROI như mẻ góc được quy vào nhóm chạm biên (`touches_edge`).
5. **Hướng nâng cấp hệ thống lên quy mô công nghiệp (YOLO / Anomalib) khi có dữ liệu thật?**
   - *Gợi ý:* Giữ nguyên hợp đồng API `InspectResponse` và tầng FastAPI, chỉ thay lõi `inspect_image()` trong `vision_engine.py`: dùng **PatchCore / Anomalib** (học không giám sát trên ảnh đạt chuẩn) để phát hiện lỗi hiếm chưa từng thấy, hoặc **YOLOv8-Seg** khi đã gán nhãn đủ nhiều mẫu lỗi để định vị và phân loại nhiều lớp phức tạp hơn.
