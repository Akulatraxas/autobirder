"""Read/write tag fields in image files (EXIF + XMP + IPTC)."""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import piexif
import piexif.helper
import pyexiv2

pyexiv2.set_log_level(4)  # mute Exiv2 warnings

MAX_TEXT = 2000
MAX_KEYWORDS = 50
MAX_KEYWORD_LEN = 100


@dataclass
class TagFields:
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    comment: str = ""


def _clean(text: str, limit: int) -> str:
    # Strip control characters (incl. NUL, which would corrupt the UTF-16 XPKeywords).
    text = "".join(ch for ch in str(text) if ch == "\n" or ch >= " " and ch != "\x7f")
    return text.strip()[:limit]


def sanitize(fields: TagFields) -> TagFields:
    seen: set[str] = set()
    keywords: list[str] = []
    for kw in fields.keywords[:MAX_KEYWORDS * 2]:
        kw = _clean(kw, MAX_KEYWORD_LEN).replace(";", ",")  # ';' separates XPKeywords
        if kw and kw.lower() not in seen:
            seen.add(kw.lower())
            keywords.append(kw)
    return TagFields(
        description=_clean(fields.description, MAX_TEXT),
        keywords=keywords[:MAX_KEYWORDS],
        comment=_clean(fields.comment, MAX_TEXT),
    )


def _is_jpeg(path: Path) -> bool:
    return path.suffix.lower() in (".jpg", ".jpeg")


def _write_exif(path: Path, f: TagFields) -> None:
    try:
        exif_dict = piexif.load(str(path))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    exif_dict["0th"][piexif.ImageIFD.ImageDescription] = f.description.encode("utf-8")
    exif_dict["0th"][piexif.ImageIFD.XPKeywords] = (";".join(f.keywords) + "\x00").encode("utf-16le")
    exif_dict["Exif"][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(
        f.comment, encoding="unicode"
    )
    piexif.insert(piexif.dump(exif_dict), str(path))


def _write_xmp_iptc(path: Path, f: TagFields) -> None:
    with pyexiv2.Image(str(path)) as img:
        img.modify_xmp({"Xmp.dc.subject": f.keywords})
        if _is_jpeg(path):
            try:
                img.modify_iptc(
                    {
                        "Iptc.Envelope.CharacterSet": "\x1b%G",  # IPTC text is UTF-8
                        "Iptc.Application2.Keywords": f.keywords,
                    }
                )
            except Exception:
                pass


def write_fields(path: Path, fields: TagFields) -> None:
    """Atomically write fields: edit a temp copy, then replace the original.
    Overwrites existing values. PNG only gets XMP."""
    f = sanitize(fields)
    st = path.stat()
    fd, tmp_name = tempfile.mkstemp(prefix=".autobirder-", suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(path, tmp)
        if _is_jpeg(path):
            _write_exif(tmp, f)
        _write_xmp_iptc(tmp, f)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # keep original mtime
    finally:
        if tmp.exists():
            tmp.unlink()


def _decode_user_comment(raw: bytes) -> str:
    try:
        return piexif.helper.UserComment.load(raw)
    except Exception:
        return ""


def read_fields(path: Path) -> TagFields | None:
    """Return the current tags, or None if the file has none of ours."""
    f = TagFields()
    if _is_jpeg(path):
        try:
            exif = piexif.load(str(path))
            desc = exif["0th"].get(piexif.ImageIFD.ImageDescription)
            if desc:
                f.description = desc.decode("utf-8", "replace").strip("\x00 ")
            f.comment = _decode_user_comment(exif["Exif"].get(piexif.ExifIFD.UserComment, b""))
            xp = exif["0th"].get(piexif.ImageIFD.XPKeywords)
            if xp:
                raw = bytes(xp) if not isinstance(xp, bytes) else xp
                f.keywords = [k for k in raw.decode("utf-16le", "replace").strip("\x00").split(";") if k]
        except Exception:
            pass
    try:
        with pyexiv2.Image(str(path)) as img:
            subject = img.read_xmp().get("Xmp.dc.subject")
            if subject:
                f.keywords = [subject] if isinstance(subject, str) else list(subject)
    except Exception:
        pass
    if not (f.description or f.keywords or f.comment):
        return None
    return f
