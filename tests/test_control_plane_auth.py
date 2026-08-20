from __future__ import annotations

from pathlib import Path

import pytest
from conftest import TEST_AIMM_API_KEY, flow_auth_headers
from fastapi.testclient import TestClient

from api.control_plane_secrets import (
    generate_secret,
    is_usable_secret,
    presented_matches,
)


def test_empty_secret_is_not_usable() -> None:
    assert not is_usable_secret("")
    assert not is_usable_secret(None)
    assert not is_usable_secret("   ")
    assert is_usable_secret("any-user-set-value")
    assert is_usable_secret(generate_secret())
    assert is_usable_secret(TEST_AIMM_API_KEY)


def test_generated_secrets_are_unique() -> None:
    a = generate_secret()
    b = generate_secret()
    assert a != b
    assert is_usable_secret(a) and is_usable_secret(b)


def test_ensure_writes_unique_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIMM_API_KEY", raising=False)
    monkeypatch.delenv("AIMM_AUTH_SECRET", raising=False)
    monkeypatch.delenv("AIMM_DISABLE_SECRET_GENERATE", raising=False)
    monkeypatch.setenv("AIMM_SECRETS_DIR", str(tmp_path))
    from api.control_plane_secrets import ensure_control_plane_secrets

    k1, s1 = ensure_control_plane_secrets(generate=True)
    k2, s2 = ensure_control_plane_secrets(generate=True)
    assert k1 == k2 and s1 == s2
    assert (tmp_path / "api_key").read_text(encoding="utf-8") == k1
    assert (tmp_path / "auth_secret").read_text(encoding="utf-8") == s1
    assert is_usable_secret(k1) and is_usable_secret(s1)


def test_user_env_secrets_are_used_not_regenerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_key = "my-own-key"
    user_secret = "my-own-secret"
    leftover_key = "leftover-generated-api-key-not-used-xx"
    leftover_secret = "leftover-generated-auth-secret-not-used"
    (tmp_path / "api_key").write_text(leftover_key, encoding="utf-8")
    (tmp_path / "auth_secret").write_text(leftover_secret, encoding="utf-8")
    monkeypatch.setenv("AIMM_API_KEY", user_key)
    monkeypatch.setenv("AIMM_AUTH_SECRET", user_secret)
    monkeypatch.delenv("AIMM_DISABLE_SECRET_GENERATE", raising=False)
    monkeypatch.setenv("AIMM_SECRETS_DIR", str(tmp_path))
    from api.control_plane_secrets import ensure_control_plane_secrets, persist_secrets

    k1, s1 = ensure_control_plane_secrets(generate=True)
    k2, s2 = ensure_control_plane_secrets(generate=True)
    assert k1 == user_key == k2
    assert s1 == user_secret == s2
    persist_secrets(k1, s1)
    assert (tmp_path / "api_key").read_text(encoding="utf-8") == user_key
    assert (tmp_path / "auth_secret").read_text(encoding="utf-8") == user_secret


def test_disable_generate_refuses_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIMM_API_KEY", raising=False)
    monkeypatch.delenv("AIMM_AUTH_SECRET", raising=False)
    monkeypatch.setenv("AIMM_DISABLE_SECRET_GENERATE", "1")
    monkeypatch.setenv("AIMM_SECRETS_DIR", str(tmp_path))
    from api.control_plane_secrets import ensure_control_plane_secrets

    with pytest.raises(RuntimeError, match="Missing"):
        ensure_control_plane_secrets(generate=True)


def test_health_is_public() -> None:
    from api.flow_stream_server import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_control_plane_rejects_missing_api_key() -> None:
    from api.flow_stream_server import app

    client = TestClient(app)
    for method, path in (
        ("get", "/deploy-config"),
        ("get", "/runtime-settings"),
        ("get", "/engine/paper/status"),
        ("post", "/engine/paper/stop"),
        ("put", "/agent-prompts/n13"),
    ):
        r = getattr(client, method)(path)
        assert r.status_code == 401, (path, r.status_code, r.text)


def test_control_plane_rejects_wrong_key() -> None:
    from api.flow_stream_server import app

    client = TestClient(app)
    for bad in ("wrong-key-not-matching", ""):
        r = client.get("/deploy-config", headers={"x-api-key": bad})
        assert r.status_code == 401, bad


def test_control_plane_accepts_configured_key() -> None:
    from api.flow_stream_server import app

    client = TestClient(app, headers=flow_auth_headers())
    r = client.get("/health")
    assert r.status_code == 200
    r = client.get("/deploy-config")
    assert r.status_code != 401


def test_loopback_does_not_bypass_auth() -> None:
    from api.flow_stream_server import app

    client = TestClient(app)
    r = client.get("/runtime-settings")
    assert r.status_code == 401


def test_compare_digest_rejects_mismatch() -> None:
    key = generate_secret()
    assert presented_matches(key, key)
    assert not presented_matches("nope", key)
    assert not presented_matches(None, key)
    assert not presented_matches(key, "")


def test_path_traversal_run_id_rejected() -> None:
    from api.flow_stream_server import app

    client = TestClient(app, headers=flow_auth_headers())
    r = client.get("/runs/../etc/passwd/events")
    assert r.status_code in {400, 404}
    r2 = client.get("/runs/%2e%2e%2fetc%2fpasswd/payload")
    assert r2.status_code in {400, 404}


def test_openapi_disabled_by_default() -> None:
    from api.flow_stream_server import app

    client = TestClient(app, headers=flow_auth_headers())
    r = client.get("/openapi.json")
    assert r.status_code in {404, 405}


def test_security_headers_present() -> None:
    from api.flow_stream_server import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


def test_jwt_secret_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIMM_AUTH_SECRET", "")
    from api.auth_routes import _jwt_secret

    with pytest.raises(RuntimeError, match="AIMM_AUTH_SECRET"):
        _jwt_secret()
    monkeypatch.setenv("AIMM_AUTH_SECRET", "any-user-secret")
    assert _jwt_secret() == "any-user-secret"


def test_require_safe_id_rejects_traversal() -> None:
    from fastapi import HTTPException

    from api.safe_ids import require_safe_id

    with pytest.raises(HTTPException) as ei:
        require_safe_id("../etc/passwd", name="run_id")
    assert ei.value.status_code == 400
    assert require_safe_id("bt_abc-1", name="run_id") == "bt_abc-1"
