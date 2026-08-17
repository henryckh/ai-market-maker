"""CLI command names: run (one period) vs windows (multi-window)."""

from __future__ import annotations

import pytest

from backtest.__main__ import main
from backtest.session import build_run_parser, build_session_parser, execute_run, execute_session


def test_help_lists_run_and_windows(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-h"]) == 0
    err = capsys.readouterr().err
    assert "python -m backtest run" in err
    assert "python -m backtest windows" in err
    assert "python -m backtest session" not in err


def test_unknown_command_mentions_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["session-typo"]) == 2
    err = capsys.readouterr().err
    assert "python -m backtest run" in err


def test_run_help_uses_one_period_wording(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", "-h"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "One-period backtest" in out
    assert "python -m backtest run" in out


def test_session_is_alias_for_run() -> None:
    assert build_session_parser is build_run_parser
    assert execute_session is execute_run
