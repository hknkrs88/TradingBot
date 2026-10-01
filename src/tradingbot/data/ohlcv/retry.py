from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def call_with_retry(
    fn: Callable[[], T],
    *,
    retry_max: int,
    backoff_seconds: float,
    sleep_fn: Callable[[float], None],
    retry_exceptions: tuple[type[BaseException], ...],
) -> T:
    attempt = 0
    while True:
        try:
            return fn()
        except retry_exceptions as exc:
            if attempt >= retry_max:
                raise
            delay = backoff_seconds * (2**attempt)
            attempt += 1
            sleep_fn(delay)
            _ = exc
