import hashlib
import io
import threading
import zipfile
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from kino.models import download
from kino.models.download import ModelError, ensure_model
from kino.models.registry import MODELS

PAYLOAD = b"fake-onnx-bytes" * 100
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("other.onnx", b"nope")
        zf.writestr("det_10g.onnx", PAYLOAD)
    return buf.getvalue()


@pytest.fixture
def server():
    hits = {"n": 0}
    files = {"/m.onnx": PAYLOAD, "/pack.zip": _zip_bytes()}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits["n"] += 1
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", hits
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def patch_spec(monkeypatch):
    def _patch(**kw):
        monkeypatch.setitem(
            MODELS, "auraface_glintr100", replace(MODELS["auraface_glintr100"], **kw)
        )

    return _patch


def test_good_checksum(server, patch_spec, tmp_path):
    url, _ = server
    patch_spec(url=url + "/m.onnx", sha256=SHA)
    p = ensure_model("auraface_glintr100", tmp_path)
    assert p.read_bytes() == PAYLOAD
    assert p.parent == tmp_path / "models"
    assert [x.name for x in p.parent.iterdir()] == [p.name]


def test_bad_checksum_leaves_no_file(server, patch_spec, tmp_path):
    url, _ = server
    patch_spec(url=url + "/m.onnx", sha256="0" * 64)
    with pytest.raises(ModelError, match="checksum mismatch"):
        ensure_model("auraface_glintr100", tmp_path)
    assert list((tmp_path / "models").iterdir()) == []


def test_http_error_leaves_no_file(server, patch_spec, tmp_path):
    url, _ = server
    patch_spec(url=url + "/missing", sha256=SHA)
    with pytest.raises(ModelError, match="HTTPStatusError"):
        ensure_model("auraface_glintr100", tmp_path)
    assert list((tmp_path / "models").iterdir()) == []


def test_missing_archive_member_is_model_error(server, monkeypatch, tmp_path):
    url, _ = server
    monkeypatch.setitem(
        MODELS,
        "scrfd_10g",
        replace(MODELS["scrfd_10g"], url=url + "/pack.zip", archive_member="absent.onnx"),
    )
    with pytest.raises(ModelError, match="absent.onnx"):
        ensure_model("scrfd_10g", tmp_path)
    assert list((tmp_path / "models").iterdir()) == []


def test_bad_zip_is_model_error(server, monkeypatch, tmp_path):
    url, _ = server
    monkeypatch.setitem(MODELS, "scrfd_10g", replace(MODELS["scrfd_10g"], url=url + "/m.onnx"))
    with pytest.raises(ModelError, match="BadZipFile"):
        ensure_model("scrfd_10g", tmp_path)
    assert list((tmp_path / "models").iterdir()) == []


def test_connection_error_is_model_error(monkeypatch, tmp_path):
    monkeypatch.setitem(
        MODELS,
        "auraface_glintr100",
        replace(MODELS["auraface_glintr100"], url="http://127.0.0.1:1/x"),
    )
    with pytest.raises(ModelError, match="ConnectError"):
        ensure_model("auraface_glintr100", tmp_path)


def test_existing_valid_not_redownloaded(server, patch_spec, tmp_path):
    url, hits = server
    patch_spec(url=url + "/m.onnx", sha256=SHA)
    ensure_model("auraface_glintr100", tmp_path)
    n = hits["n"]
    ensure_model("auraface_glintr100", tmp_path)
    assert hits["n"] == n


def test_corrupt_existing_replaced(server, patch_spec, tmp_path):
    url, hits = server
    patch_spec(url=url + "/m.onnx", sha256=SHA)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "glintr100.onnx").write_bytes(b"corrupt")
    p = ensure_model("auraface_glintr100", tmp_path)
    assert p.read_bytes() == PAYLOAD
    assert hits["n"] == 1


def test_zip_member_extraction(server, monkeypatch, tmp_path):
    url, _ = server
    monkeypatch.setitem(
        MODELS, "scrfd_10g", replace(MODELS["scrfd_10g"], url=url + "/pack.zip", sha256=SHA)
    )
    p = ensure_model("scrfd_10g", tmp_path)
    assert p.name == "det_10g.onnx"
    assert p.read_bytes() == PAYLOAD
    assert [x.name for x in p.parent.iterdir()] == ["det_10g.onnx"]


def test_unknown_model(tmp_path):
    with pytest.raises(ModelError, match="unknown"):
        ensure_model("nope", tmp_path)


def test_refuses_http_redirect(tmp_path):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/x")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ModelError, match="non-https"):
            download._download(f"http://127.0.0.1:{srv.server_port}/", tmp_path / "x")
    finally:
        srv.shutdown()
    srv.server_close()


def test_buffalo_warns_noncommercial(server, monkeypatch, tmp_path):
    from loguru import logger

    url, _ = server
    monkeypatch.setitem(
        MODELS,
        "buffalo_l_r50",
        replace(
            MODELS["buffalo_l_r50"],
            url=url + "/pack.zip",
            sha256=SHA,
            archive_member="det_10g.onnx",
        ),
    )
    msgs: list[str] = []
    h = logger.add(lambda m: msgs.append(str(m)), level="WARNING")
    try:
        ensure_model("buffalo_l_r50", tmp_path)
    finally:
        logger.remove(h)
    assert any("non-commercial" in m for m in msgs)


@pytest.mark.models
@pytest.mark.parametrize("name", sorted(MODELS))
def test_real_urls_respond(name):
    r = httpx.head(MODELS[name].url, follow_redirects=False, timeout=30)
    assert r.status_code in (200, 302)


def test_registry_shape():
    assert set(MODELS) == {"scrfd_10g", "auraface_glintr100", "buffalo_l_r50"}
    for spec in MODELS.values():
        assert len(spec.sha256) == 64
