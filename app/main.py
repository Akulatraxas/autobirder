"""Autobirder web app: FastAPI backend."""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .classifier import BioclipPredictor, Predictor, Worker
from .metadata import TagFields, read_fields, sanitize, write_fields
from .scanner import ImageIndex, signature
from .store import Store

log = logging.getLogger("autobirder")

STATIC_DIR = Path(__file__).parent / "static"
ID_RE = re.compile(r"^[0-9a-f]{16}$")
CSRF_HEADER = "x-autobirder"
PREVIEW_MAX = 1800
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
RATE_LIMIT_PER_MIN = 1500

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
        "object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class FieldsModel(BaseModel):
    description: str = Field("", max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    comment: str = Field("", max_length=2000)


class SaveRequest(BaseModel):
    fields: FieldsModel


class PrioritizeRequest(BaseModel):
    ids: list[str] = Field(max_length=20)


class KeywordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=100)


def _fields_json(f: TagFields | None) -> dict | None:
    if f is None:
        return None
    return {"description": f.description, "keywords": f.keywords, "comment": f.comment}


class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0):
        self._limit, self._window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self._window:
                q.popleft()
            if len(q) >= self._limit:
                return False
            q.append(now)
            return True


def create_app(data_dir: Path, cache_dir: Path, predictor: Predictor | None = None,
               allowed_hosts: list[str] | None = None) -> FastAPI:
    index = ImageIndex(data_dir)
    store = Store(cache_dir / "autobirder.sqlite")
    worker = Worker(index, store, predictor or BioclipPredictor())
    preview_dir = cache_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(RATE_LIMIT_PER_MIN)
    write_lock = threading.Lock()

    app = FastAPI(title="Autobirder", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["localhost", "127.0.0.1"]
    )

    # TODO(security): there is no authentication (explicitly out of scope: local/LAN tool).
    # Publish the port on 127.0.0.1 only, or put a reverse proxy with auth in front of it.
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "POST", "HEAD"):
            return JSONResponse({"detail": "Method not allowed"}, status_code=405)
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            return JSONResponse({"detail": "Too many requests"}, status_code=429)
        if request.method == "POST":
            # CSRF: a custom header can't be sent cross-site without a CORS preflight,
            # and we don't enable CORS.
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def resolve(image_id: str) -> Path:
        if not ID_RE.fullmatch(image_id):
            raise HTTPException(404, "Not found")
        try:
            return index.path(image_id)
        except (KeyError, OSError):
            raise HTTPException(404, "Not found")

    @app.get("/api/images")
    def list_images():
        marks = store.get_marks()
        items = []
        for image_id in index.ids():
            rel = index.rel(image_id)
            items.append({"id": image_id, "path": rel, "mark": marks.get(rel)})
        return {"images": items}

    @app.get("/api/images/{image_id}")
    def image_detail(image_id: str):
        path = resolve(image_id)
        rel = index.rel(image_id)
        status = worker.status(image_id)
        return {
            "id": image_id,
            "path": rel,
            "status": status,
            "candidates": worker.candidates(image_id) if status == "ready" else [],
            "current": _fields_json(read_fields(path)),
            "mark": store.get_mark(rel),
        }

    @app.get("/api/images/{image_id}/status")
    def image_status(image_id: str):
        resolve(image_id)
        return {"status": worker.status(image_id)}

    @app.get("/api/images/{image_id}/preview")
    def image_preview(image_id: str):
        from PIL import Image, ImageOps

        path = resolve(image_id)
        cached = preview_dir / f"{image_id}-{signature(path).replace(':', '_')}.jpg"
        if not cached.exists():
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=85)
            tmp = cached.with_suffix(".tmp")
            tmp.write_bytes(buf.getvalue())
            os.replace(tmp, cached)
        return FileResponse(cached, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/images/{image_id}/file")
    def image_file(image_id: str):
        path = resolve(image_id)
        return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))

    @app.post("/api/prioritize")
    def prioritize(body: PrioritizeRequest):
        ids = [i for i in body.ids if ID_RE.fullmatch(i)]
        valid = []
        for i in ids:
            try:
                index.rel(i)
                valid.append(i)
            except KeyError:
                pass
        worker.prioritize(valid)
        return {"queued": len(valid)}

    @app.post("/api/images/{image_id}/save")
    def save(image_id: str, body: SaveRequest):
        path = resolve(image_id)
        rel = index.rel(image_id)
        fields = sanitize(TagFields(**body.fields.model_dump()))
        try:
            with write_lock:
                write_fields(path, fields)
        except Exception:
            log.exception("Writing tags failed for image id %s", image_id)
            raise HTTPException(500, "Could not write tags to the file")
        store.restamp(rel, signature(path))
        store.set_mark(rel, "saved")
        # Remember only manually chosen tags: species/genus names come from the guesses anyway.
        auto = set()
        for c in worker.candidates(image_id):
            auto.update(str(c.get(k, "")).lower() for k in ("common_name", "scientific_name", "genus"))
        store.record_keywords([k for k in fields.keywords if k.lower() not in auto])
        return {"ok": True, "fields": _fields_json(fields)}

    @app.get("/api/keywords")
    def keywords(q: str = Query("", max_length=100),
                 exclude: list[str] = Query(default_factory=list, max_length=50)):
        return {"keywords": store.suggest_keywords(q.strip(), [e[:100] for e in exclude])}

    @app.post("/api/keywords/forget")
    def forget_keyword(body: KeywordRequest):
        store.forget_keyword(body.keyword.strip())
        return {"ok": True}

    @app.post("/api/images/{image_id}/skip")
    def skip(image_id: str):
        resolve(image_id)
        store.set_mark(index.rel(image_id), "skipped")
        return {"ok": True}

    @app.post("/api/rescan")
    def rescan():
        index.rescan()
        return {"count": len(index.ids())}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/")
    def root():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.state.worker = worker
    return app


def _from_env() -> FastAPI:
    hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
    return create_app(
        Path(os.environ.get("DATA_DIR", "/data")),
        Path(os.environ.get("CACHE_DIR", "/cache")),
        allowed_hosts=hosts,
    )
