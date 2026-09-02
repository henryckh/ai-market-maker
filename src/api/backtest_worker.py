"""Claim queued tenant backtests from .runs/backtests/*/job.json and execute them.

Used by deploy/docker-compose.yml flow-worker:
  python -m api.backtest_worker
"""

from __future__ import annotations

import os
import time

from api.backtest_routes import execute_queued_backtest_job, iter_queued_backtest_ids


def main() -> None:
    interval = float((os.getenv("AIMM_BACKTEST_WORKER_INTERVAL_SEC") or "2").strip() or "2")
    interval = max(0.5, min(30.0, interval))
    batch = int((os.getenv("AIMM_BACKTEST_WORKER_BATCH") or "4").strip() or "4")
    batch = max(1, min(16, batch))
    print(f"[backtest-worker] start interval={interval}s batch={batch}", flush=True)
    while True:
        try:
            ids = iter_queued_backtest_ids(limit=batch)
            for rid in ids:
                print(f"[backtest-worker] claim {rid}", flush=True)
                execute_queued_backtest_job(rid)
        except Exception as e:
            print(f"[backtest-worker] error {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
