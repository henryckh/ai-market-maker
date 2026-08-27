from __future__ import annotations

import json
from pathlib import Path

from api.tenant_deploy import write_tenant_deploy


def test_write_tenant_deploy_not_active_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMM_RUNS_DIR", str(tmp_path))
    dest = write_tenant_deploy(
        deploy={
            "agents": {"technical_ta_engine": {"enabled": True, "weight": 1, "llm_enabled": False}}
        },
        run_id="bt-abc123def456",
        user_id="user-1",
    )
    assert dest.is_file()
    assert "tenants" in dest.parts
    assert dest.name == "deploy.json"
    assert "deploy.active.json" not in str(dest)
    obj = json.loads(Path(dest).read_text(encoding="utf-8"))
    assert "technical_ta_engine" in obj["agents"]
