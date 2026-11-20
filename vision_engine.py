"""OpenCV surface-defect inspection engine for brushed-metal products."""

import base64
import time
import cv2
import numpy as np

IMG_W, IMG_H = 600, 400
ROI_X1, ROI_Y1, ROI_X2, ROI_Y2 = 44, 36, 556, 364
MIN_DEFECT_AREA = 120.0
CRACK_AREA_THRESHOLD = 900.0
SCRATCH_ASPECT_THRESHOLD = 3.2
BINARY_THRESHOLD = 26

_K25 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
_K7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))


def _normalize_bgr(bgr_img: np.ndarray) -> np.ndarray:
    """Validate input array and normalize to 600x400 3-channel BGR."""
    if bgr_img is None or bgr_img.size == 0:
        raise ValueError("Empty image input")
    if bgr_img.ndim == 2:
        bgr = cv2.cvtColor(bgr_img, cv2.COLOR_GRAY2BGR)
    elif bgr_img.ndim == 3 and bgr_img.shape[2] == 4:
        bgr = cv2.cvtColor(bgr_img, cv2.COLOR_BGRA2BGR)
    elif bgr_img.ndim == 3 and bgr_img.shape[2] == 3:
        bgr = bgr_img
    else:
        raise ValueError("Unsupported image channel format")
    return cv2.resize(bgr, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA)


def _classify_defect(x: int, y: int, w: int, h: int, area: float) -> tuple[str, str]:
    """Classify a defect contour into Vietnamese label and ASCII tag."""
    touches_edge = (x <= 50) or (y <= 42) or (x + w >= 550) or (y + h >= 358)
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect >= SCRATCH_ASPECT_THRESHOLD and not touches_edge:
        return "Vết xước bề mặt", "Scratch"
    if area >= CRACK_AREA_THRESHOLD or touches_edge:
        return "Nứt vỡ / Biến dạng", "Crack"
    return "Vết ố / Dị vật", "Stain"


def _draw_tag(img: np.ndarray, text: str, x: int, y: int, h: int, bg: tuple[int, int, int]) -> None:
    """Draw a compact ASCII label near (x, y) without clipping past image bounds."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, 0.45, 1)
    pad_x, pad_y = 5, 4
    box_w, box_h = tw + pad_x * 2, th + pad_y * 2
    lx = min(max(x, 2), IMG_W - box_w - 2)
    ly = y - box_h - 2
    if ly < 2:
        ly = y + h + 2
    if ly + box_h > IMG_H - 2:
        ly = max(2, y + 2)
    cv2.rectangle(img, (lx, ly), (lx + box_w, ly + box_h), bg, -1)
    cv2.putText(img, text, (lx + pad_x, ly + box_h - pad_y - 1), font, 0.45, (255, 255, 255), 1, cv2.LINE_AA)


def _encode_png_data_uri(img: np.ndarray) -> str:
    """Encode BGR image as base64 PNG data URI."""
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def inspect_image(bgr_img: np.ndarray) -> dict:
    """Run surface-defect inspection pipeline and return structured metrics and images."""
    t0 = time.perf_counter()
    resized = _normalize_bgr(bgr_img)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    roi = blur[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2]
    blackhat = cv2.morphologyEx(roi, cv2.MORPH_BLACKHAT, _K25)
    tophat = cv2.morphologyEx(roi, cv2.MORPH_TOPHAT, _K25)
    morph_diff = cv2.max(blackhat, tophat)
    local_bg = cv2.GaussianBlur(roi, (151, 151), 0, borderType=cv2.BORDER_REPLICATE)
    bg_diff = cv2.absdiff(roi, local_bg)
    combined = cv2.max(morph_diff, bg_diff)

    _, thresh = cv2.threshold(combined, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY)
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, _K7)

    full_mask = np.zeros((IMG_H, IMG_W), dtype=np.uint8)
    full_mask[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2] = closed
    contours, _ = cv2.findContours(full_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area >= MIN_DEFECT_AREA:
            x, y, w, h = cv2.boundingRect(cnt)
            candidates.append((y, x, w, h, area, cnt))
    candidates.sort(key=lambda item: (item[0], item[1]))

    filtered_mask = np.zeros((IMG_H, IMG_W), dtype=np.uint8)
    accepted_contours = []
    defects = []
    for idx, (y, x, w, h, area, cnt) in enumerate(candidates, start=1):
        label, tag = _classify_defect(x, y, w, h, area)
        accepted_contours.append(cnt)
        cv2.drawContours(filtered_mask, [cnt], -1, 255, cv2.FILLED)
        defects.append({
            "id": idx,
            "type": label,
            "tag": tag,
            "area": int(round(area)),
            "bbox": [int(x), int(y), int(w), int(h)],
        })

    defect_count = len(defects)
    status = "PASS" if defect_count == 0 else "DEFECT"
    defect_ratio = round((cv2.countNonZero(filtered_mask) / (IMG_W * IMG_H)) * 100.0, 2)

    combined_full = np.zeros((IMG_H, IMG_W), dtype=np.float32)
    combined_full[ROI_Y1:ROI_Y2, ROI_X1:ROI_X2] = combined.astype(np.float32)
    heat_gray = np.clip(combined_full * 3.0, 0.0, 255.0).astype(np.uint8)
    processed_mask = cv2.applyColorMap(heat_gray, cv2.COLORMAP_JET)
    processed_mask[:ROI_Y1, :] = (15, 23, 42)
    processed_mask[ROI_Y2:, :] = (15, 23, 42)
    processed_mask[:, :ROI_X1] = (15, 23, 42)
    processed_mask[:, ROI_X2:] = (15, 23, 42)
    if accepted_contours:
        cv2.drawContours(processed_mask, accepted_contours, -1, (255, 255, 255), 1, cv2.LINE_AA)

    annotated = resized.copy()
    if status == "PASS":
        cv2.rectangle(annotated, (ROI_X1, ROI_Y1), (ROI_X2 - 1, ROI_Y2 - 1), (34, 197, 94), 2)
        _draw_tag(annotated, "PASS", ROI_X1 + 6, ROI_Y1 + 26, 0, (22, 163, 74))
    else:
        cv2.rectangle(annotated, (ROI_X1, ROI_Y1), (ROI_X2 - 1, ROI_Y2 - 1), (100, 116, 139), 1)
        for item in defects:
            x, y, w, h = item["bbox"]
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (38, 38, 220), 2)
            _draw_tag(annotated, f"#{item['id']}: {item['tag']}", x, y, h, (38, 38, 220))

    process_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "status": status,
        "defect_count": defect_count,
        "defect_ratio": defect_ratio,
        "process_time_ms": process_time_ms,
        "defects": defects,
        "original_image": _encode_png_data_uri(resized),
        "processed_mask": _encode_png_data_uri(processed_mask),
        "annotated_image": _encode_png_data_uri(annotated),
    }
