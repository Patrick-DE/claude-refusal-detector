"""CachedOracle runner adding cache, budget tracking, retries, and fail-loud semantics."""

import hashlib
import time
from refusal_detector.logger import get_logger
from refusal_detector.ports import Cache, Oracle, Verdict

logger = get_logger("runner")


def hash_prompt(prompt: str) -> str:
    """Generate SHA256 hex digest of prompt text."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class MaxCallsExceededError(RuntimeError):
    """Raised when oracle call budget is exhausted."""

    pass


class CachedOracle(Oracle):
    """Oracle wrapper providing per-session memoization, max-call budget, and retry logic.

    Budget semantics: ``total_calls`` counts distinct prompts successfully evaluated.
    Retries of the same prompt (429/529/timeout) do NOT consume the ``max_calls`` budget.
    """

    def __init__(
        self,
        base_oracle: Oracle,
        cache: Cache,
        max_calls: int = 50,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
    ) -> None:
        self.base_oracle = base_oracle
        self.cache = cache
        self.max_calls = max_calls
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self.total_calls = 0
        self.cache_hits = 0

    def test(self, prompt: str) -> Verdict:
        """Test prompt string with cache checking and call limit enforcement."""
        prompt_hash = hash_prompt(prompt)

        # Check Cache
        cached_verdict = self.cache.get(prompt_hash)
        if cached_verdict is not None:
            self.cache_hits += 1
            logger.debug("Cache hit for hash %s (blocked=%s)", prompt_hash[:8], cached_verdict.blocked)
            return cached_verdict

        # Check Call Budget
        if self.total_calls >= self.max_calls:
            logger.error("Call budget exhausted (%d/%d calls made)", self.total_calls, self.max_calls)
            raise MaxCallsExceededError(f"Call budget of {self.max_calls} calls exceeded.")

        # Execute call with retry handling
        verdict = self._execute_with_retry(prompt)
        self.total_calls += 1

        # Store in cache
        self.cache.set(prompt_hash, verdict)
        logger.debug(
            "Oracle call #%d: hash=%s, blocked=%s, reason=%s",
            self.total_calls,
            prompt_hash[:8],
            verdict.blocked,
            verdict.reason_class,
        )

        return verdict

    def _execute_with_retry(self, prompt: str) -> Verdict:
        """Execute base oracle call with exponential backoff on transient errors."""
        attempt = 0
        while True:
            try:
                # Base oracle call: any adapter error must be allowed to bubble up (fail loud)
                return self.base_oracle.test(prompt)
            except Exception as exc:
                attempt += 1
                # Check for rate limit / transient error indicators in exception
                err_str = str(exc).lower()
                is_transient = "429" in err_str or "529" in err_str or "rate limit" in err_str or "timeout" in err_str

                if is_transient and attempt <= self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "Transient error on oracle call (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt,
                        self.max_retries,
                        exc,
                        sleep_time,
                    )
                    time.sleep(sleep_time)
                else:
                    # Fail loud - re-raise exception
                    logger.error("Oracle call failed permanently: %s", exc)
                    raise
