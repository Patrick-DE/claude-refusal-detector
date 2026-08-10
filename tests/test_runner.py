"""Unit tests for CachedOracle runner."""

import pytest

from refusal_detector.adapters import FakeOracleAdapter, InMemoryCache
from refusal_detector.ports import ReasonClass, Verdict
from refusal_detector.runner import CachedOracle, MaxCallsExceededError


def test_cached_oracle_memoization():
    base_oracle = FakeOracleAdapter(triggers=["BLOCK_ME"])
    cache = InMemoryCache()
    runner = CachedOracle(base_oracle, cache=cache, max_calls=10)

    # First call - cache miss
    v1 = runner.test("This contains BLOCK_ME")
    assert v1.blocked is True
    assert runner.total_calls == 1
    assert runner.cache_hits == 0

    # Second call with identical prompt - cache hit
    v2 = runner.test("This contains BLOCK_ME")
    assert v2.blocked is True
    assert runner.total_calls == 1  # call count did not increase
    assert runner.cache_hits == 1


def test_cached_oracle_max_calls_budget():
    base_oracle = FakeOracleAdapter(triggers=["BLOCK_ME"])
    cache = InMemoryCache()
    runner = CachedOracle(base_oracle, cache=cache, max_calls=2)

    runner.test("Prompt 1")
    runner.test("Prompt 2")

    assert runner.total_calls == 2
    with pytest.raises(MaxCallsExceededError):
        runner.test("Prompt 3")


def test_cached_oracle_fail_loud():
    class FailingOracle:
        def test(self, prompt: str) -> Verdict:
            raise RuntimeError("API Auth Error")

    cache = InMemoryCache()
    runner = CachedOracle(FailingOracle(), cache=cache, max_calls=5)

    with pytest.raises(RuntimeError, match="API Auth Error"):
        runner.test("Any prompt")
