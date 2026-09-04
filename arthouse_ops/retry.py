"""Retry with exponential backoff and jitter.

n8n gave every node a "retry on fail" checkbox. This is that checkbox, written
out. Two rules it adds that the checkbox did not have: only retryable failures
are retried, so a bad API key fails immediately instead of three times, and a
Retry-After header from the server wins over the computed backoff.
"""

import random
import time

from . import logs

log = logs.get("retry")


class Retryable(Exception):
    """Wraps a failure worth trying again, with an optional server-set delay."""

    def __init__(self, cause, retry_after=None):
        super().__init__(str(cause))
        self.cause = cause
        self.retry_after = retry_after


def call(fn, *, attempts=5, base=1.0, cap=60.0, classify, op, **fields):
    """Run fn(), retrying while classify() says the failure is retryable.

    classify(exception) returns None for a permanent failure or a Retryable for
    one worth another attempt. Sleeps base * 2**n seconds with full jitter,
    capped, unless the server named its own delay.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classify decides what is fatal
            retryable = classify(exc)
            if retryable is None or attempt == attempts:
                log.error(
                    "giving up", op=op, attempt=attempt,
                    retryable=retryable is not None, cause=str(exc)[:200], **fields,
                )
                raise
            delay = retryable.retry_after
            if delay is None:
                delay = random.uniform(0, min(cap, base * (2 ** (attempt - 1))))
            log.warn(
                "retrying", op=op, attempt=attempt, wait_s=round(delay, 2),
                cause=str(exc)[:200], **fields,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")
