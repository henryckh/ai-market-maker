from concurrent.futures import ThreadPoolExecutor

from llm.usage import record_usage, reset_usage, snapshot_usage


def test_usage_counts_across_worker_threads():
    reset_usage()

    def _one(_i: int) -> None:
        record_usage(prompt_tokens=3, completion_tokens=1, calls=1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_one, range(8)))

    snap = snapshot_usage()
    assert snap["llm_calls"] == 8
    assert snap["prompt_tokens"] == 24
    assert snap["completion_tokens"] == 8
    reset_usage()
    assert snapshot_usage()["llm_calls"] == 0
