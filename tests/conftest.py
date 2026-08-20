from __future__ import annotations

import os

import pytest

TEST_AIMM_API_KEY = "pytest-aimm-api-key-not-for-production-use"
TEST_AIMM_AUTH_SECRET = "pytest-aimm-auth-secret-not-for-production"


@pytest.fixture(autouse=True)
def _control_plane_test_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIMM_API_KEY", TEST_AIMM_API_KEY)
    monkeypatch.setenv("AIMM_AUTH_SECRET", TEST_AIMM_AUTH_SECRET)
    monkeypatch.setenv("AIMM_DISABLE_SECRET_GENERATE", "1")


def flow_auth_headers() -> dict[str, str]:
    return {"x-api-key": os.environ["AIMM_API_KEY"]}
