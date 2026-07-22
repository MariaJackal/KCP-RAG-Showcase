"""#6: 每個回應都應帶基本安全標頭，且不影響正常回應。"""


def test_security_headers_on_root(client):
    r = client.get("/")
    csp = r.headers.get("Content-Security-Policy")
    assert csp and "script-src 'self'; " in csp  # 一般頁面不放行 inline script
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'self'" in csp  # 簡報頁 iframe 實機展示（方案 A）需要同源可框
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert r.headers.get("Referrer-Policy") == "no-referrer"
    assert "max-age" in (r.headers.get("Strict-Transport-Security") or "")


def test_security_headers_on_api_and_still_ok(client):
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_slides_csp_allows_inline_script_only_there(client):
    r = client.get("/static/slides.html")
    if r.status_code == 404:
        import pytest
        pytest.skip("slides.html 未包含在公開版（屬簡報素材，已移除）")
    assert r.status_code == 200
    csp = r.headers.get("Content-Security-Policy")
    assert csp and "script-src 'self' 'unsafe-inline'" in csp
    # 其他路徑維持嚴格 script-src
    r2 = client.get("/")
    assert "script-src 'self'; " in r2.headers.get("Content-Security-Policy")
