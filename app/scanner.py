"""Recursive image discovery and a server-side id <-> path index.

The client never sends paths. It only sends opaque ids that map to files found
under the data root, which prevents path traversal.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _natural_key(text: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def _path_key(rel: str) -> list:
    return [_natural_key(part) for part in Path(rel).parts]


def scan(root: Path) -> list[str]:
    """Return relative paths of all images below root, naturally sorted.

    Hidden files/folders and symlinks are ignored.
    """
    root = root.resolve()
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in EXTENSIONS:
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            found.append(os.path.relpath(full, root))
    found.sort(key=_path_key)
    return found


def make_id(rel: str) -> str:
    return hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]


def signature(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


class ImageIndex:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._order: list[str] = []
        self._by_id: dict[str, str] = {}
        self.rescan()

    def rescan(self) -> None:
        rels = scan(self.root)
        by_id = {make_id(r): r for r in rels}
        self._by_id = by_id  # atomic swap
        self._order = [make_id(r) for r in rels]

    def ids(self) -> list[str]:
        return list(self._order)

    def rel(self, image_id: str) -> str:
        return self._by_id[image_id]  # KeyError if unknown

    def path(self, image_id: str) -> Path:
        """Resolve an id to a real file strictly inside the root."""
        rel = self._by_id[image_id]
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root) or not p.is_file():
            raise KeyError(image_id)
        return p
