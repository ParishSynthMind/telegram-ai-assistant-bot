"""Tests against a fake local Telegram server; no network, no real bot."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import bridge


class Fake:
    updates = []
    sent = []
    status = 200
    file_bytes = b"FAKEBYTES"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(Fake.file_bytes)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        params = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        method = self.path.rsplit("/", 1)[-1]
        if Fake.status != 200:
            self.send_response(Fake.status); self.end_headers(); self.wfile.write(b"{}"); return
        if method == "getUpdates":
            off = int(params.get("offset", 0))
            result = [u for u in Fake.updates if u["update_id"] >= off]
        elif method == "getFile":
            result = {"file_path": "files/x.bin", "file_size": 9}
        else:
            if method == "sendMessage":
                Fake.sent.append(params)
            result = True
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "result": result}).encode())


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(bridge, "API_BASE", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(bridge, "POLL_TIMEOUT", 0)
    monkeypatch.setattr(bridge, "MEDIA_DIR", tmp_path / "media")
    for name in ("OFFSET_FILE", "LOG_FILE", "UNKNOWN_LOG"):
        monkeypatch.setattr(bridge, name, tmp_path / name.lower())
    alerts = []
    monkeypatch.setattr(bridge, "alert_failure", lambda *a, **k: alerts.append(a))
    Fake.updates, Fake.sent, Fake.status = [], [], 200
    yield alerts
    srv.shutdown()


def upd(i, uid, text="hi"):
    m = {"from": {"id": uid, "first_name": "X"}, "chat": {"id": uid}}
    if text is not None:
        m["text"] = text
    return {"update_id": i, "message": m}


async def echo(t):
    return f"echo: {t}"


def test_allowed_user_gets_reply():
    Fake.updates = [upd(1, 111, "hello")]
    assert bridge.poll_once("T", {111}, echo) == 1
    assert Fake.sent[0]["text"] == "echo: hello" and Fake.sent[0]["chat_id"] == "111"


def test_unknown_sender_ignored_and_id_logged_without_text(tmp_path):
    Fake.updates = [upd(1, 999, "secret words")]
    bridge.poll_once("T", {111}, echo)
    assert Fake.sent == []
    logged = bridge.UNKNOWN_LOG.read_text()
    assert "id=999" in logged and "secret" not in logged


def test_offset_advances_so_no_replay():
    Fake.updates = [upd(1, 111, "a")]
    bridge.poll_once("T", {111}, echo)
    bridge.poll_once("T", {111}, echo)
    assert len(Fake.sent) == 1 and bridge.read_offset() == 2


def test_unsupported_type_gets_polite_note():
    Fake.updates = [upd(1, 111, None)]
    bridge.poll_once("T", {111}, echo)
    assert "can't read yet" in Fake.sent[0]["text"]


def test_photo_is_downloaded_prompted_and_deleted(tmp_path):
    seen = {}

    async def spy(t):
        seen["p"] = t
        return "nice"
    u = upd(1, 111, None)
    u["message"]["photo"] = [{"file_id": "small0000"}, {"file_id": "bigphoto1"}]
    u["message"]["caption"] = "what is this"
    Fake.updates = [u]
    bridge.poll_once("T", {111}, spy)
    assert "saved at" in seen["p"] and "what is this" in seen["p"] and "never instructions" in seen["p"]
    assert Fake.sent[0]["text"] == "nice"
    assert list((tmp_path / "media").glob("*")) == []  # cleaned up


def test_voice_note_is_transcribed(monkeypatch):
    seen = {}

    async def spy(t):
        seen["p"] = t
        return "ok"
    monkeypatch.setattr(bridge, "transcribe", lambda p: "remind me about the school run")
    u = upd(1, 111, None)
    u["message"]["voice"] = {"file_id": "voicefile1"}
    Fake.updates = [u]
    bridge.poll_once("T", {111}, spy)
    assert "school run" in seen["p"] and "Voice note" in seen["p"]


def test_media_failure_apologises_and_alerts(env, monkeypatch):
    def boom(p):
        raise RuntimeError("whisper missing")
    monkeypatch.setattr(bridge, "transcribe", boom)
    u = upd(1, 111, None)
    u["message"]["voice"] = {"file_id": "voicefile1"}
    Fake.updates = [u]
    bridge.poll_once("T", {111}, echo)
    assert "Couldn't process" in Fake.sent[0]["text"] and env


def test_failure_apologises_and_alerts(env):
    async def boom(t):
        raise RuntimeError("login expired")
    Fake.updates = [upd(1, 111)]
    bridge.poll_once("T", {111}, boom)
    assert "snag" in Fake.sent[0]["text"] and env and "login expired" in env[0][1]


def test_bad_token_raises_auth_error():
    Fake.status = 401
    with pytest.raises(bridge.TelegramAuthError):
        bridge.poll_once("T", {111}, echo)


def test_long_reply_is_split():
    parts = bridge.split_text("word " * 3000)
    assert len(parts) > 1 and all(len(p) <= 4096 for p in parts)
    assert "".join(parts).replace(" ", "") == ("word" * 3000)
