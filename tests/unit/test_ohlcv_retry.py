import ccxt
import pytest

from tradingbot.data.ohlcv.retry import call_with_retry


def test_retry_then_success() -> None:
    sleeps: list[float] = []
    state = {"n": 0}

    def fn() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise ccxt.RateLimitExceeded("slow")
        return "ok"

    result = call_with_retry(
        fn,
        retry_max=3,
        backoff_seconds=0.5,
        sleep_fn=sleeps.append,
        retry_exceptions=(ccxt.RateLimitExceeded,),
    )
    assert result == "ok"
    assert sleeps == [0.5, 1.0]


def test_retry_exhausted() -> None:
    def fn() -> str:
        raise ccxt.NetworkError("down")

    with pytest.raises(ccxt.NetworkError):
        call_with_retry(
            fn,
            retry_max=2,
            backoff_seconds=0.1,
            sleep_fn=lambda _d: None,
            retry_exceptions=(ccxt.NetworkError,),
        )
