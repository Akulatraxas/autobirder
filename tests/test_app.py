import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.metadata import TagFields, read_fields, write_fields
from app.scanner import scan

H = {"X-Autobirder": "1"}


class FakePredictor:
    def __init__(self):
        self.calls = 0

    def predict(self, path):
        self.calls += 1
        return [
            {"common_name": "Eurasian Blue Tit", "scientific_name": "Cyanistes caeruleus",
             "genus": "Cyanistes", "score": 0.8},
            {"common_name": "Great Tit", "scientific_name": "Parus major", "genus": "Parus", "score": 0.1},
        ]


def make_img(path: Path, fmt="JPEG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), (10, 120, 30)).save(path, fmt)


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    make_img(data / "a" / "img10.jpg")
    make_img(data / "a" / "img2.JPG")
    make_img(data / "b" / "pic.png", "PNG")
    (data / "notes.txt").write_text("x")
    make_img(data / ".hidden" / "no.jpg")
    pred = FakePredictor()
    app = create_app(data, tmp_path / "cache", pred)
    client = TestClient(app, base_url="http://localhost")
    return data, client, pred


def test_scan_recursive_natural_sorted(env):
    data, _, _ = env
    assert scan(data) == ["a/img2.JPG", "a/img10.jpg", "b/pic.png"]


@pytest.mark.parametrize("name,fmt", [("t.jpg", "JPEG"), ("t.png", "PNG")])
def test_metadata_roundtrip(tmp_path, name, fmt):
    p = tmp_path / name
    make_img(p, fmt)
    before = p.stat().st_mtime_ns
    write_fields(p, TagFields("Blaumeise (Cyanistes caeruleus)", ["Blaumeise", "Cyanistes", "Käfer; x"], "BioCLIP ü"))
    got = read_fields(p)
    assert got is not None
    assert got.keywords == ["Blaumeise", "Cyanistes", "Käfer, x"]
    assert p.stat().st_mtime_ns == before
    assert not [f for f in tmp_path.iterdir() if f.name.startswith(".autobirder-")]
    if fmt == "JPEG":
        assert got.description == "Blaumeise (Cyanistes caeruleus)"
        assert got.comment == "BioCLIP ü"
    # overwrite
    write_fields(p, TagFields("x", ["only"], "c"))
    assert read_fields(p).keywords == ["only"]
    Image.open(p).load()  # still a valid image


def test_api_flow(env):
    data, c, pred = env
    imgs = c.get("/api/images").json()["images"]
    assert [i["path"] for i in imgs] == ["a/img2.JPG", "a/img10.jpg", "b/pic.png"]
    iid = imgs[0]["id"]
    assert c.post("/api/prioritize", json={"ids": [iid]}, headers=H).status_code == 200
    for _ in range(50):
        d = c.get(f"/api/images/{iid}").json()
        if d["status"] == "ready":
            break
        time.sleep(0.1)
    assert d["status"] == "ready" and d["candidates"][0]["scientific_name"] == "Cyanistes caeruleus"
    assert d["current"] is None
    assert c.get(f"/api/images/{iid}/preview").headers["content-type"] == "image/jpeg"
    r = c.post(f"/api/images/{iid}/save", headers=H,
               json={"fields": {"description": "d", "keywords": ["k1", "k2"], "comment": "c"}})
    assert r.status_code == 200
    d = c.get(f"/api/images/{iid}").json()
    assert d["mark"] == "saved" and d["current"]["keywords"] == ["k1", "k2"]
    assert d["status"] == "ready"  # cache survives our own write
    n = pred.calls
    c.post("/api/prioritize", json={"ids": [iid]}, headers=H)
    time.sleep(0.3)
    assert pred.calls == n
    assert c.post(f"/api/images/{imgs[1]['id']}/skip", headers=H).status_code == 200


def test_security(env):
    _, c, _ = env
    iid = c.get("/api/images").json()["images"][0]["id"]
    assert c.get("/api/images/..%2f..%2fetc%2fpasswd").status_code == 404
    assert c.get("/api/images/0123456789abcdef").status_code == 404
    assert c.get("/api/images/%2e%2e/file").status_code in (404, 405)
    # CSRF header required
    assert c.post(f"/api/images/{iid}/skip").status_code == 403
    # method allow-list
    assert c.request("DELETE", "/api/images").status_code == 405
    assert c.request("PUT", "/api/rescan", headers=H).status_code == 405
    # host allow-list
    bad = TestClient(c.app, base_url="http://evil.example")
    assert bad.get("/api/images").status_code == 400
    # headers
    r = c.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    # healthcheck
    hz = c.get("/healthz")
    assert hz.status_code == 200 and hz.json() == {"status": "ok"}
    # validation
    big = {"fields": {"description": "x" * 5000}}
    assert c.post(f"/api/images/{iid}/save", json=big, headers=H).status_code == 422
    assert c.get("/api/images").headers["cache-control"] == "no-store"


def test_keyword_autocomplete(env):
    _, c, _ = env
    imgs = c.get("/api/images").json()["images"]

    def save(i, kws):
        iid = imgs[i]["id"]
        c.post("/api/prioritize", json={"ids": [iid]}, headers=H)
        for _ in range(50):
            if c.get(f"/api/images/{iid}/status").json()["status"] == "ready":
                break
            time.sleep(0.1)
        r = c.post(f"/api/images/{iid}/save", headers=H,
                   json={"fields": {"description": "d", "keywords": kws, "comment": ""}})
        assert r.status_code == 200

    def sug(q="", exclude=()):
        return c.get("/api/keywords", params={"q": q, "exclude": list(exclude)}).json()["keywords"]

    assert sug() == []
    # species/genus names from the guesses are not remembered; manual tags are
    save(0, ["Eurasian Blue Tit", "Cyanistes", "Berlin Zoo", "Berlin"])
    save(1, ["berlin zoo", "Garden"])
    assert sug() == ["Berlin Zoo", "Garden", "Berlin"]       # most used first, first spelling kept
    assert sug("ber") == ["Berlin Zoo", "Berlin"]
    assert sug("ZOO") == ["Berlin Zoo"]                       # substring, case-insensitive
    assert sug("", exclude=["berlin zoo"]) == ["Garden", "Berlin"]
    assert sug("%") == [] and sug("_") == []                  # LIKE wildcards are escaped
    assert c.post("/api/keywords/forget", json={"keyword": "garden"}, headers=H).status_code == 200
    assert "Garden" not in sug()
    assert c.post("/api/keywords/forget", json={"keyword": "x"}).status_code == 403  # CSRF header
    assert c.get("/api/keywords", params={"q": "x" * 200}).status_code == 422


def test_no_innerhtml():
    js = (Path(__file__).parent.parent / "app" / "static" / "app.js").read_text()
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "alert("):
        assert bad not in js
