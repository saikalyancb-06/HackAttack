"""with_retry (A2). CONTRACTS §3."""

import time


class RetryExhausted(Exception):
    def __init__(self, last_error):
        super().__init__(str(last_error))
        self.last_error = last_error


def with_retry(fn, attempts: int = 3, backoff: tuple = (1, 2, 4)):
    last_error = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if i < attempts - 1:
                delay = backoff[min(i, len(backoff) - 1)] if backoff else 0
                if delay > 0:
                    time.sleep(delay)
    raise RetryExhausted(last_error)


if __name__ == "__main__":
    t0 = time.time()
    # 1. Success first try
    res = with_retry(lambda: 42, attempts=3, backoff=(0, 0))
    assert res == 42

    # 2. Success on attempt 3
    state = {"calls": 0}

    def flaky():
        state["calls"] += 1
        if state["calls"] < 3:
            raise ValueError(f"fail {state['calls']}")
        return "success"

    res = with_retry(flaky, attempts=3, backoff=(0, 0))
    assert res == "success"
    assert state["calls"] == 3

    # 3. Exhaust retries raises RetryExhausted
    failed = False
    try:
        def bad():
            raise RuntimeError("permanent failure")
        with_retry(bad, attempts=3, backoff=(0, 0))
    except RetryExhausted as e:
        failed = True
        assert "permanent failure" in str(e)
        assert isinstance(e.last_error, RuntimeError)
    assert failed

    print(f"ledger.retry self-check PASSED in {time.time() - t0:.3f}s")
