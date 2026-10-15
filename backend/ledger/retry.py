"""with_retry(fn, attempts=3, backoff=(1,2,4)) -> result, raises RetryExhausted(last_error)."""
from __future__ import annotations

import time
from typing import Any, Callable

from backend import config


class RetryExhausted(Exception):
    def __init__(self, last_error: BaseException, attempts: int = 0):
        super().__init__(f"failed after {attempts} attempts: {last_error}")
        self.last_error = last_error
        self.attempts = attempts


class NonRetryable(Exception):
    """Raise (or wrap) to stop retrying immediately, e.g. bad arguments."""


def with_retry(fn: Callable[[], Any], attempts: int = 3, backoff: tuple = (1, 2, 4),
               on_error: Callable[[int, BaseException], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except NonRetryable:
            raise
        except Exception as e:  # the caller decides fallback; we only retry
            last = e
            if on_error:
                on_error(i + 1, e)
            if i < attempts - 1:
                delay = backoff[min(i, len(backoff) - 1)] * config.RETRY_BACKOFF_SCALE
                end = time.time() + delay
                while time.time() < end:
                    if should_stop and should_stop():
                        raise RetryExhausted(last, i + 1)
                    time.sleep(min(0.1, max(0.0, end - time.time())))
    raise RetryExhausted(last, attempts)


if __name__ == "__main__":
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert with_retry(flaky, backoff=(0, 0, 0)) == "ok" and calls["n"] == 3
    try:
        with_retry(lambda: 1 / 0, attempts=2, backoff=(0,))
        raise AssertionError("should raise")
    except RetryExhausted as e:
        assert isinstance(e.last_error, ZeroDivisionError) and e.attempts == 2
    print("ledger.retry self-check OK")
