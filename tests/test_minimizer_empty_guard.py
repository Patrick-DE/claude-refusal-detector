"""A blocked input must never minimize to zero segments.

Observed in a real run: after two genuine trigger groups were found, further rounds
each returned a 0-segment "trigger", so the caller's remaining-set never shrank and the
loop spun. An empty candidate is also not probeable - the Messages API rejects an empty
prompt with HTTP 400.
"""

from refusal_detector.minimizer import Minimizer
from refusal_detector.ports import ReasonClass, Segment, Verdict


class _AlwaysBlockedOracle:
    """Worst case for ddmin: every candidate looks blocked, including tiny ones."""

    def __init__(self) -> None:
        self.calls = 0

    def test(self, prompt: str) -> Verdict:
        self.calls += 1
        return Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL)


def _segments(count: int) -> list[Segment]:
    return [
        Segment(index=i, text=f"line {i}", start_char=0, end_char=6, start_line=i + 1, end_line=i + 1)
        for i in range(count)
    ]


def test_blocked_input_never_minimizes_to_nothing():
    trigger, verdict = Minimizer(_AlwaysBlockedOracle()).minimize(_segments(8))

    assert trigger, "a blocked input must yield at least one segment, never an empty trigger"
    assert verdict.blocked is True


def test_empty_input_is_returned_untouched_without_probing():
    oracle = _AlwaysBlockedOracle()

    trigger, _ = Minimizer(oracle).minimize([])

    assert trigger == []
    assert oracle.calls == 0, "an empty candidate must never be sent to the oracle (HTTP 400)"


def test_guard_replaces_an_empty_ddmin_result_with_the_first_segment():
    """Direct proof the final guard in minimize() fires.

    test_blocked_input_never_minimizes_to_nothing above passes even without the guard:
    _coarse_pass/_ddmin_pass have their own loop invariant (they never run below 2
    segments and only ever reassign the current set to a provably non-empty candidate or
    chunk) that already keeps them from reaching zero on their own. That makes the guard
    real insurance for a saturation case the current algorithm cannot reach unaided today,
    not proof it fires under everyday operation. To falsify the guard itself, force the
    exact failure it exists for - the same technique test_minimizer.py already uses in
    test_mutation_check_fake_oracle_failure to simulate a broken pass.
    """

    class _DdminReturnsNothing(Minimizer):
        def _ddmin_pass(self, segments, initial_verdict):
            return [], initial_verdict

    segs = _segments(8)
    trigger, verdict = _DdminReturnsNothing(_AlwaysBlockedOracle()).minimize(segs)

    assert trigger == [segs[0]], "guard must fall back to the smallest non-empty candidate"
    assert verdict.blocked is True
