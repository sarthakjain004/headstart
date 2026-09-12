from io import BytesIO
from urllib.error import HTTPError

from headstart.alerts import delivery_http


def test_http_failure_bounds_the_body_and_omits_the_request_url():
    error = HTTPError("https://example.invalid/private-token", 429, "refused", {}, BytesIO(b"x" * 300))
    reason = delivery_http.reason(error)
    assert reason == "HTTP 429: " + "x" * 200
    assert "private-token" not in reason


def test_post_sends_once_and_returns_the_json_reply(monkeypatch):
    calls = []

    def open_request(request, timeout):
        calls.append((request.data, request.get_method(), timeout))
        return BytesIO(b'{"ok": true}')

    monkeypatch.setattr(delivery_http.urllib.request, "urlopen", open_request)
    assert delivery_http.post("https://example.invalid", b"payload", {}) == {"ok": True}
    assert calls == [(b"payload", "POST", 30)]
