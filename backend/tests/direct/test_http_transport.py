import asyncio
import ssl
from types import SimpleNamespace

import httpx

from app.direct import pairing
from app.direct.http_transport import verified_tls_context


def test_trust_store_is_reused_with_certificate_and_hostname_checks():
    first = verified_tls_context()
    assert first is verified_tls_context()
    assert first.verify_mode == ssl.CERT_REQUIRED
    assert first.check_hostname is True


def test_fresh_identity_checks_keep_cookies_and_credentials_per_viewer(monkeypatch):
    real_client = httpx.AsyncClient
    clients, credentials = [], []

    def handler(request):
        cookie = request.headers.get("cookie", "")
        credentials.append(cookie)
        if request.url.path.endswith("/auth/me"):
            return httpx.Response(
                200,
                json={"organization_id": "org", "is_active": True, "role": "member"},
                headers={"Set-Cookie": "foreign=first-viewer; Path=/"},
            )
        if request.url.path.endswith("/administration/capabilities"):
            return httpx.Response(200, json={"can_manage": False})
        return httpx.Response(200, json=[{"id": "allowed-zone"}])

    def client(**kwargs):
        assert kwargs["verify"] is verified_tls_context()
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        value = real_client(**kwargs, transport=httpx.MockTransport(handler))
        clients.append(value)
        return value

    monkeypatch.setattr(pairing.httpx, "AsyncClient", client)

    async def check():
        cfg = SimpleNamespace(dashboard_api_url="https://dashboard.test/api/v1")
        for name in ["viewer-one", "viewer-two"]:
            request = SimpleNamespace(headers={}, cookies={"access_token": name})
            identity = await pairing.dashboard_identity(request, cfg)
            assert identity["allowed_zone_ids"] == ["allowed-zone"]

    asyncio.run(check())
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert all(value.is_closed for value in clients)
    assert len(credentials) == 6  # All three authorization checks run for each viewer.
    assert credentials[0] == "access_token=viewer-one"
    assert credentials[3] == "access_token=viewer-two"
    assert "foreign=" not in credentials[3]
