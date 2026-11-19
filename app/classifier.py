"""Background classification worker around BioCLIP."""
from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

from .scanner import ImageIndex, signature
from .store import Store

log = logging.getLogger("autobirder.classifier")

TOP_K = 5


class Predictor(Protocol):
    def predict(self, path: str) -> list[dict]: ...


class BioclipPredictor:
    """Wraps pybioclip's TreeOfLifeClassifier (loaded lazily, once)."""

    def __init__(self, top_k: int = TOP_K):
        self._top_k = top_k
        self._clf = None
        self._rank = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._clf is None:
                t0 = time.monotonic()
                log.info("Loading BioCLIP model (first run downloads ~4 GB of weights)...")
                from bioclip import Rank, TreeOfLifeClassifier  # heavy import

                self._clf = TreeOfLifeClassifier()
                self._rank = Rank.SPECIES
                log.info("BioCLIP model loaded in %.1fs", time.monotonic() - t0)

    def predict(self, path: str) -> list[dict]:
        self.load()
        preds = self._clf.predict(path, self._rank, k=self._top_k)
        out = []
        for p in preds:
            scientific = (p.get("species") or "").strip()
            genus = (p.get("genus") or (scientific.split(" ")[0] if scientific else "")).strip()
            out.append(
                {
                    "common_name": (p.get("common_name") or "").strip(),
                    "scientific_name": scientific,
                    "genus": genus,
                    "score": float(p.get("score", 0.0)),
                }
            )
        out.sort(key=lambda c: c["score"], reverse=True)
        return out[: self._top_k]


class Worker:
    """Single worker thread. Only classifies images the client asks for
    (current image + a few ahead), newest request first."""

    def __init__(self, index: ImageIndex, store: Store, predictor: Predictor):
        self._index = index
        self._store = store
        self._predictor = predictor
        self._cond = threading.Condition()
        self._prio: list[str] = []
        self._state: dict[str, str] = {}  # id -> "classifying" | "error"
        has_loader = callable(getattr(predictor, "load", None))
        self._model_status = "loading" if has_loader else "ready"
        self._model_started: float | None = time.monotonic() if has_loader else None
        self._load_done: threading.Event | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="classifier", daemon=True)
        self._thread.start()

    def model_info(self) -> dict[str, str | int]:
        """Return current model status ('loading' | 'ready' | 'error') and elapsed_s while loading."""
        with self._cond:
            info: dict[str, str | int] = {"status": self._model_status}
            if self._model_status == "loading" and self._model_started is not None:
                info["elapsed_s"] = max(0, int(time.monotonic() - self._model_started))
            return info

    def shutdown(self) -> None:
        with self._cond:
            self._stop = True
            if self._load_done is not None:
                self._load_done.set()
            self._cond.notify_all()

    def prioritize(self, ids: list[str]) -> None:
        with self._cond:
            for i in ids:
                if self._state.get(i) == "error":
                    del self._state[i]  # allow retry on explicit request
            self._prio = list(ids)
            self._cond.notify_all()

    def status(self, image_id: str) -> str:
        """pending | classifying | ready | error. KeyError for unknown ids."""
        state = self._state.get(image_id)
        if state:
            return state
        rel = self._index.rel(image_id)
        try:
            sig = signature(self._index.path(image_id))
        except (OSError, KeyError):
            return "error"
        return "ready" if self._store.get_candidates(rel, sig) is not None else "pending"

    def candidates(self, image_id: str) -> list[dict]:
        rel = self._index.rel(image_id)
        sig = signature(self._index.path(image_id))
        return self._store.get_candidates(rel, sig) or []

    def _pick(self) -> str | None:
        while self._prio:
            image_id = self._prio.pop(0)
            if self._state.get(image_id) in ("classifying", "error"):
                continue
            try:
                rel = self._index.rel(image_id)
                sig = signature(self._index.path(image_id))
            except (KeyError, OSError):
                continue
            if self._store.get_candidates(rel, sig) is not None:
                continue
            return image_id
        return None

    def _load_model(self, loader) -> None:
        done = threading.Event()
        with self._cond:
            self._model_status = "loading"
            if self._model_started is None:
                self._model_started = time.monotonic()
            self._load_done = done

        def heartbeat() -> None:
            while not done.wait(30.0):
                with self._cond:
                    if self._stop or self._model_status != "loading" or self._model_started is None:
                        return
                    elapsed = int(time.monotonic() - self._model_started)
                log.info("Still loading BioCLIP model (%ds elapsed)...", elapsed)

        threading.Thread(target=heartbeat, name="classifier-heartbeat", daemon=True).start()
        try:
            loader()
            with self._cond:
                self._model_status = "ready"
                self._model_started = None
        except Exception:
            with self._cond:
                self._model_status = "error"
                self._model_started = None
            raise
        finally:
            done.set()
            with self._cond:
                if self._load_done is done:
                    self._load_done = None

    def _run(self) -> None:
        loader = getattr(self._predictor, "load", None)
        if callable(loader):
            try:
                self._load_model(loader)
            except Exception:
                log.exception("Model preload failed; will retry on first job")
        while True:
            with self._cond:
                image_id = None
                while not self._stop:
                    image_id = self._pick()
                    if image_id:
                        break
                    self._cond.wait()
                if self._stop:
                    return
                self._state[image_id] = "classifying"
                need_load = callable(loader) and self._model_status != "ready"
            try:
                if need_load:
                    self._load_model(loader)
                path = self._index.path(image_id)
                rel = self._index.rel(image_id)
                sig = signature(path)
                candidates = self._predictor.predict(str(path))
                self._store.put_candidates(rel, sig, candidates)
                with self._cond:
                    self._state.pop(image_id, None)
            except Exception:
                log.exception("Classification failed for image id %s", image_id)
                with self._cond:
                    self._state[image_id] = "error"
