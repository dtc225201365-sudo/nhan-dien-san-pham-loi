"""FastAPI backend for surface-defect inspection with SQLite WAL history and security controls."""

import io, logging, re, secrets, sqlite3, time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import cv2, numpy as np, uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sample_generator import ensure_samples
from vision_engine import inspect_image

Image.MAX_IMAGE_PIXELS, MAX_UPLOAD_BYTES, RATE_MAX, RATE_WIN = 25_000_000, 10 * 1024 * 1024, 120, 60.0
BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR, STATIC_DIR, DB_PATH = BASE_DIR / "samples", BASE_DIR / "static", BASE_DIR / "history.db"
ALLOWED_EXTS, ALLOWED_FORMATS, STATIC_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}, {"PNG", "JPEG", "WEBP", "BMP"}, {".css", ".js", ".svg", ".png", ".woff2"}
SAMPLES = [("mau_1_chuan.png", "1. Đạt chuẩn"), ("mau_2_vet_xuoc.png", "2. Vết xước"), ("mau_3_vet_o_ban.png", "3. Vết ố"), ("mau_4_nut_vo_goc.png", "4. Mẻ góc"), ("mau_5_chuan_2.png", "5. Chuẩn 2")]
SAMPLE_MAP = dict(SAMPLES)
SEC_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()", "Cache-Control": "no-store"}
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
_log = logging.getLogger("inspection")

class SampleItem(BaseModel): name: str; label: str
class DefectItem(BaseModel): id: int; type: str; tag: str; area: int; bbox: list[int]
class _InspectionBase(BaseModel):
    id: int; filename: str; status: Literal["PASS", "DEFECT"]
    defect_count: int; defect_ratio: float; process_time_ms: float; created_at: str
class InspectResponse(_InspectionBase):
    defects: list[DefectItem]; original_image: str; processed_mask: str; annotated_image: str
class HistorySummary(BaseModel): total: int; passed: int; defected: int; pass_rate: float
class HistoryItem(_InspectionBase): defect_summary: str
class HistoryResponse(BaseModel): summary: HistorySummary; items: list[HistoryItem]
class ClearHistoryResponse(BaseModel): deleted: int; message: str

@contextmanager
def _db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        with conn: yield conn
    finally: conn.close()

def _decode_bgr(raw: bytes, status_code: int, detail: str) -> np.ndarray:
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None or bgr.size == 0: raise HTTPException(status_code=status_code, detail=detail)
    return bgr

def _decode_upload(raw_name: str, raw: bytes) -> tuple[str, np.ndarray]:
    # TODO(security): Verified purely in-memory with Pillow/OpenCV (no disk write); add AV/CDR scanning before multi-tenant deployment.
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", re.split(r"[\\/]+", raw_name)[-1]).strip()
    ext = Path(cleaned).suffix.lower()
    if not cleaned or ext not in ALLOWED_EXTS or not cleaned[: -len(ext)] or not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="Dữ liệu không hợp lệ.")
    source_name = cleaned if len(cleaned) <= 120 else cleaned[: 120 - len(ext)] + ext
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ALLOWED_FORMATS: raise ValueError("Unsupported format")
            im.verify()
        with Image.open(io.BytesIO(raw)) as im2: im2.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise HTTPException(status_code=422, detail="Dữ liệu không hợp lệ.")
    return source_name, _decode_bgr(raw, 422, "Dữ liệu không hợp lệ.")

def _load_sample(sample_name: str) -> tuple[str, np.ndarray]:
    if not sample_name or len(sample_name) > 120 or any(ch in sample_name for ch in ("/", "\\", "..", "\x00")) or any(ord(c) < 32 or ord(c) == 127 for c in sample_name):
        raise HTTPException(status_code=422, detail="Dữ liệu không hợp lệ.")
    if sample_name not in SAMPLE_MAP: raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu.")
    root = SAMPLES_DIR.resolve()
    resolved = (root / sample_name).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file(): raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu.")
    return sample_name, _decode_bgr(resolved.read_bytes(), 404, "Không tìm thấy dữ liệu.")

@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_samples(SAMPLES_DIR)
    with _db_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS inspections (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, status TEXT NOT NULL, defect_count INTEGER NOT NULL, defect_ratio REAL NOT NULL, process_time_ms REAL NOT NULL, defect_summary TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_inspections_id_desc ON inspections(id DESC)")
    yield

# TODO(security): Single-user localhost (127.0.0.1) demo skips auth/CSRF; enforce OAuth2/JWT + CSRF before network exposure.
app = FastAPI(title="Inspection API", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def _validation_err(_: Request, __: RequestValidationError) -> JSONResponse: return JSONResponse(status_code=422, content={"detail": "Dữ liệu không hợp lệ."})

@app.exception_handler(Exception)
async def _unhandled_err(_: Request, exc: Exception) -> JSONResponse:
    _log.exception("Unhandled server error: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Mất kết nối máy chủ. Thử lại sau."})

@app.middleware("http")
async def security_and_rate_limit(request: Request, call_next) -> Response:
    if request.url.path.startswith("/api/"):
        ip, now = (request.client.host if request.client else "unknown"), time.monotonic()
        bucket = _rate_buckets[ip]
        while bucket and bucket[0] < now - RATE_WIN: bucket.popleft()
        if len(bucket) >= RATE_MAX: return JSONResponse(status_code=429, content={"detail": "Quá nhiều yêu cầu. Thử lại sau."})
        bucket.append(now)
    response = await call_next(request)
    for k, v in SEC_HEADERS.items(): response.headers.setdefault(k, v)
    return response

@app.get("/", response_class=HTMLResponse)
async def serve_index() -> Response:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file(): raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu.")
    nonce = secrets.token_urlsafe(16)
    csp = f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8").replace("__CSP_NONCE__", nonce), headers={"Content-Security-Policy": csp})

@app.get("/favicon.ico")
async def favicon() -> Response: return Response(status_code=204)

@app.get("/static/{asset_path:path}")
async def serve_static(asset_path: str) -> FileResponse:
    static_root = STATIC_DIR.resolve()
    resolved = (static_root / asset_path).resolve()
    if not resolved.is_relative_to(static_root) or resolved.name == "index.html" or resolved.suffix.lower() not in STATIC_EXTS or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy dữ liệu.")
    return FileResponse(resolved)

@app.get("/api/samples", response_model=list[SampleItem])
async def get_samples() -> list[SampleItem]: return [SampleItem(name=name, label=label) for name, label in SAMPLES]

@app.post("/api/inspect", response_model=InspectResponse)
async def inspect_endpoint(file: UploadFile | None = File(default=None), sample_name: str | None = Form(default=None)) -> InspectResponse:
    if (file is not None) == (sample_name is not None): raise HTTPException(status_code=422, detail="Dữ liệu không hợp lệ.")
    source_name, bgr = _decode_upload(file.filename or "", await file.read(MAX_UPLOAD_BYTES + 1)) if file is not None else _load_sample(sample_name or "")
    result = inspect_image(bgr)
    summary_text = ", ".join(dict.fromkeys(d["type"] for d in result["defects"])) if result["defects"] else "Đạt chuẩn"
    created_at = datetime.now(timezone.utc).isoformat()
    with _db_conn() as conn:
        row_id = int(conn.execute("INSERT INTO inspections (filename, status, defect_count, defect_ratio, process_time_ms, defect_summary, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (source_name, result["status"], result["defect_count"], result["defect_ratio"], result["process_time_ms"], summary_text, created_at)).lastrowid or 0)
    return InspectResponse(id=row_id, filename=source_name, created_at=created_at, **result)

@app.get("/api/history", response_model=HistoryResponse)
async def get_history() -> HistoryResponse:
    with _db_conn() as conn:
        agg = conn.execute("SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN status='PASS' THEN 1 ELSE 0 END),0) AS passed, COALESCE(SUM(CASE WHEN status='DEFECT' THEN 1 ELSE 0 END),0) AS defected FROM inspections").fetchone()
        rows = conn.execute("SELECT id, filename, status, defect_count, defect_ratio, process_time_ms, defect_summary, created_at FROM inspections ORDER BY id DESC LIMIT ?", (50,)).fetchall()
    total, passed, defected = int(agg["total"]), int(agg["passed"]), int(agg["defected"])
    return HistoryResponse(summary=HistorySummary(total=total, passed=passed, defected=defected, pass_rate=round((passed / total) * 100.0, 1) if total > 0 else 0.0), items=[HistoryItem(**dict(r)) for r in rows])

@app.delete("/api/history", response_model=ClearHistoryResponse)
async def clear_history() -> ClearHistoryResponse:
    with _db_conn() as conn: deleted = int(conn.execute("DELETE FROM inspections").rowcount or 0)
    return ClearHistoryResponse(deleted=deleted, message="Đã xóa")

if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", port=8050)
