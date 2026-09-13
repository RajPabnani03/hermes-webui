"""Regression tests for #7550: unauthenticated request with a body desyncs keep-alive.

When ``check_auth`` rejected a request carrying a body, the body stayed
unread in ``rfile``. On a keep-alive connection the leftover bytes were then
parsed as the next request line, so the following request failed with
``501 Unsupported method`` (e.g. ``'{"session_id":"x"}GET'``).

These tests pin the fix: ``check_auth`` drains the body before writing the
early 401/302, and falls back to ``close_connection`` when the body cannot
be framed reliably (chunked, invalid or oversized Content-Length).
"""

import io
from urllib.parse import urlparse

from http.server import BaseHTTPRequestHandler

import api.auth as auth
from server import Handler


class _FakeHandler:
    """Minimal BaseHTTPRequestHandler stand-in for check_auth."""

    def __init__(self, body: bytes = b"", headers=None):
        self.headers = dict(headers or {})
        if body and "Content-Length" not in self.headers:
            self.headers["Content-Length"] = str(len(body))
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = []
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers.append((name, value))

    def end_headers(self):
        pass


def _unauth(monkeypatch):
    """Force auth on with no valid session (avoids the PBKDF2 startup cost)."""
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)


def test_401_drains_body_for_keep_alive_reuse(monkeypatch):
    _unauth(monkeypatch)
    body = b'{"session_id":"x","text":"y"}'
    handler = _FakeHandler(body=body, headers={"Content-Type": "application/json"})

    assert auth.check_auth(handler, urlparse("/api/chat/steer")) is False
    assert handler.status == 401
    # Body fully consumed so the next pipelined request parses cleanly.
    assert handler.rfile.read() == b""
    assert handler.close_connection is False


def test_302_redirect_drains_body(monkeypatch):
    _unauth(monkeypatch)
    body = b'{"session_id":"x","text":"y"}'
    handler = _FakeHandler(body=body)

    assert auth.check_auth(handler, urlparse("/some/page")) is False
    assert handler.status == 302
    assert handler.rfile.read() == b""
    assert handler.close_connection is False


def test_401_without_body_still_rejects(monkeypatch):
    _unauth(monkeypatch)
    handler = _FakeHandler()

    assert auth.check_auth(handler, urlparse("/api/chat/steer")) is False
    assert handler.status == 401
    assert handler.close_connection is False


def test_chunked_body_falls_back_to_close(monkeypatch):
    _unauth(monkeypatch)
    handler = _FakeHandler(
        body=b"1a\r\n" + b"x" * 26 + b"\r\n0\r\n\r\n",
        headers={"Transfer-Encoding": "chunked"},
    )

    assert auth.check_auth(handler, urlparse("/api/chat/steer")) is False
    assert handler.status == 401
    assert handler.close_connection is True


def test_invalid_content_length_falls_back_to_close(monkeypatch):
    _unauth(monkeypatch)
    handler = _FakeHandler(body=b"{}", headers={"Content-Length": "bogus"})

    assert auth.check_auth(handler, urlparse("/api/chat/steer")) is False
    assert handler.status == 401
    assert handler.close_connection is True


def test_oversized_body_falls_back_to_close(monkeypatch):
    _unauth(monkeypatch)
    handler = _FakeHandler(body=b"x", headers={"Content-Length": str(21 * 1024 * 1024)})

    assert auth.check_auth(handler, urlparse("/api/chat/steer")) is False
    assert handler.status == 401
    assert handler.close_connection is True


def test_authenticated_request_leaves_body_for_handler(monkeypatch):
    _unauth(monkeypatch)
    monkeypatch.setattr(auth, "verify_session", lambda value: True)
    body = b'{"session_id":"x","text":"y"}'
    handler = _FakeHandler(
        body=body,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"{auth.COOKIE_NAME}=tok.sig",
        },
    )

    assert auth.check_auth(handler, urlparse("/api/chat/steer")) is True
    # check_auth must not touch the body on the allow path.
    assert handler.rfile.read() == body
    assert handler.status is None


def test_options_preflight_response_is_framed(monkeypatch):
    monkeypatch.setattr(BaseHTTPRequestHandler, "end_headers", lambda self: None)
    sent = []
    handler = Handler.__new__(Handler)
    handler.headers = {}
    handler.rfile = io.BytesIO(b"")
    handler.close_connection = False
    handler.send_response = lambda status: setattr(handler, "status", status)
    handler.send_header = lambda key, value: sent.append((key, value))

    Handler.do_OPTIONS(handler)

    assert dict(sent).get("Content-Length") == "0"
