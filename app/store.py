"""SQLite cache for classification results and per-image review marks."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS results ("
                "relpath TEXT PRIMARY KEY, sig TEXT NOT NULL, candidates TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS marks ("
                "relpath TEXT PRIMARY KEY, mark TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS keywords ("
                "keyword TEXT PRIMARY KEY COLLATE NOCASE, uses INTEGER NOT NULL, last_used REAL NOT NULL)"
            )
            self._conn.commit()

    def record_keywords(self, keywords: list[str]) -> None:
        """Remember keywords for autocomplete (first-seen spelling wins)."""
        now = time.time()
        with self._lock:
            for kw in keywords:
                self._conn.execute(
                    "INSERT INTO keywords (keyword, uses, last_used) VALUES (?, 1, ?) "
                    "ON CONFLICT(keyword) DO UPDATE SET uses = uses + 1, last_used = excluded.last_used",
                    (kw, now),
                )
            self._conn.commit()

    def suggest_keywords(self, query: str, exclude: list[str], limit: int = 8) -> list[str]:
        """Prefix matches first, then substring matches; most used / most recent first."""
        esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            rows = self._conn.execute(
                "SELECT keyword FROM keywords WHERE keyword LIKE ? ESCAPE '\\' "
                "ORDER BY (keyword LIKE ? ESCAPE '\\') DESC, uses DESC, last_used DESC LIMIT ?",
                (f"%{esc}%", f"{esc}%", limit + len(exclude)),
            ).fetchall()
        skip = {e.lower() for e in exclude}
        return [r[0] for r in rows if r[0].lower() not in skip][:limit]

    def forget_keyword(self, keyword: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
            self._conn.commit()

    def get_candidates(self, rel: str, sig: str) -> list[dict] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT candidates FROM results WHERE relpath = ? AND sig = ?", (rel, sig)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put_candidates(self, rel: str, sig: str, candidates: list[dict]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO results (relpath, sig, candidates) VALUES (?, ?, ?)",
                (rel, sig, json.dumps(candidates)),
            )
            self._conn.commit()

    def restamp(self, rel: str, sig: str) -> None:
        """Keep the cached result valid after we modified the file ourselves."""
        with self._lock:
            self._conn.execute("UPDATE results SET sig = ? WHERE relpath = ?", (sig, rel))
            self._conn.commit()

    def get_marks(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT relpath, mark FROM marks").fetchall()
        return dict(rows)

    def get_mark(self, rel: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT mark FROM marks WHERE relpath = ?", (rel,)
            ).fetchone()
        return row[0] if row else None

    def set_mark(self, rel: str, mark: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO marks (relpath, mark) VALUES (?, ?)", (rel, mark)
            )
            self._conn.commit()
