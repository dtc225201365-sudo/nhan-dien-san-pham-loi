"""Generate deterministic synthetic brushed-metal inspection sample images."""

from pathlib import Path
import cv2
import numpy as np

IMG_W, IMG_H = 600, 400
TRAY_BGR = (38, 42, 48)
PLATE_BOX = (32, 24, 568, 376)  # x1, y1, x2, y2
BEVEL_BGR = (128, 134, 142)
SAMPLE_FILES = (
    "mau_1_chuan.png",
    "mau_2_vet_xuoc.png",
    "mau_3_vet_o_ban.png",
    "mau_4_nut_vo_goc.png",
    "mau_5_chuan_2.png",
)


def _create_plate(
    base_bgr: tuple[int, int, int],
    seed: int,
    horiz_span: float = 10.0,
    diag_span: float = 0.0,
) -> np.ndarray:
    """Build a 600x400 tray image containing a brushed-metal plate."""
    img = np.full((IMG_H, IMG_W, 3), TRAY_BGR, dtype=np.uint8)
    x1, y1, x2, y2 = PLATE_BOX
    h, w = y2 - y1, x2 - x1

    plate = np.full((h, w, 3), base_bgr, dtype=np.float32)
    if diag_span > 0.0:
        gx = np.linspace(-diag_span / 2.0, diag_span / 2.0, w, dtype=np.float32)
        gy = np.linspace(-diag_span / 2.0, diag_span / 2.0, h, dtype=np.float32)
        plate += gx[None, :, None] + gy[:, None, None]
    else:
        gx = np.linspace(-horiz_span, horiz_span, w, dtype=np.float32)
        plate += gx[None, :, None]

    rng = np.random.default_rng(seed)
    streak = rng.integers(-2, 3, size=(h, 1, 1), dtype=np.int16)
    grain = rng.integers(-1, 2, size=(h, w, 1), dtype=np.int16)
    plate += (streak + grain).astype(np.float32)

    img[y1:y2, x1:x2] = np.clip(plate, 0, 255).astype(np.uint8)
    cv2.rectangle(img, (x1, y1), (x2 - 1, y2 - 1), BEVEL_BGR, thickness=2)
    return img


def _build_sample(filename: str) -> np.ndarray:
    """Create the BGR image corresponding to one sample filename."""
    if filename == "mau_1_chuan.png":
        return _create_plate((192, 195, 200), seed=101, horiz_span=10.0)

    if filename == "mau_2_vet_xuoc.png":
        img = _create_plate((192, 195, 200), seed=102, horiz_span=10.0)
        cv2.line(img, (115, 115), (255, 132), (38, 40, 44), 2, cv2.LINE_AA)
        cv2.line(img, (295, 260), (450, 236), (38, 40, 44), 2, cv2.LINE_AA)
        return img

    if filename == "mau_3_vet_o_ban.png":
        img = _create_plate((192, 195, 200), seed=103, horiz_span=10.0)
        cv2.ellipse(img, (233, 174), (13, 10), 15, 0, 360, (58, 68, 82), -1, cv2.LINE_AA)
        cv2.circle(img, (244, 179), 5, (58, 68, 82), -1, cv2.LINE_AA)
        cv2.ellipse(img, (390, 245), (10, 8), -20, 0, 360, (58, 68, 82), -1, cv2.LINE_AA)
        return img

    if filename == "mau_4_nut_vo_goc.png":
        img = _create_plate((192, 195, 200), seed=104, horiz_span=10.0)
        chip = np.array(
            [(486, 24), (568, 24), (568, 126), (520, 112), (496, 72)],
            dtype=np.int32,
        )
        cv2.fillPoly(img, [chip], (30, 32, 36), cv2.LINE_AA)
        cv2.line(img, (512, 92), (480, 128), (30, 32, 36), 3, cv2.LINE_AA)
        cv2.line(img, (480, 128), (462, 145), (30, 32, 36), 3, cv2.LINE_AA)
        return img

    if filename == "mau_5_chuan_2.png":
        return _create_plate((170, 176, 184), seed=105, diag_span=16.0)

    raise ValueError(f"Unknown sample filename: {filename}")


def ensure_samples(samples_dir: Path, force: bool = False) -> None:
    """Ensure all 5 PNG sample files exist in samples_dir."""
    samples_dir.mkdir(parents=True, exist_ok=True)
    for name in SAMPLE_FILES:
        target = samples_dir / name
        if force or not target.is_file():
            img = _build_sample(name)
            cv2.imwrite(str(target), img)


if __name__ == "__main__":
    ensure_samples(Path(__file__).resolve().parent / "samples", force=True)
    print("Generated 5 sample images successfully.")
