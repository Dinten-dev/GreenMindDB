"""Get-only credentials must never be asked to inspect or create buckets."""

from types import SimpleNamespace

from app.services import wav_service


def test_read_only_factory_does_not_require_bucket_admin_rights(monkeypatch):
    calls = []
    client = SimpleNamespace(
        head_bucket=lambda **_: calls.append("head"),
        create_bucket=lambda **_: calls.append("create"),
    )
    monkeypatch.setenv("S3_READ_ONLY", "true")
    monkeypatch.setattr(wav_service, "_s3_client", None)
    monkeypatch.setattr(wav_service.boto3, "client", lambda *_, **__: client)
    assert wav_service._get_s3_client() is client
    assert calls == []
