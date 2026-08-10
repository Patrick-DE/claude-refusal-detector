"""Unit tests for delta debugging minimizer algorithm."""

import pytest

from refusal_detector.adapters import FakeOracleAdapter
from refusal_detector.input_loader import LineSegmenter
from refusal_detector.minimizer import Minimizer
from refusal_detector.ports import ReasonClass


def test_minimizer_single_trigger_line():
    lines = [f"Line {i}: Normal content" for i in range(1, 11)]
    lines[4] = "Line 5: DANGEROUS_WORD payload"
    prompt = "\n".join(lines)

    segmenter = LineSegmenter()
    segments = segmenter.split(prompt)

    fake_oracle = FakeOracleAdapter(triggers=["DANGEROUS_WORD"])
    minimizer = Minimizer(fake_oracle)

    trigger_segments, verdict = minimizer.minimize(segments)

    assert len(trigger_segments) == 1
    assert trigger_segments[0].index == 4
    assert "DANGEROUS_WORD" in trigger_segments[0].text
    assert verdict.blocked is True
    assert verdict.reason_class == ReasonClass.STRUCTURED_REFUSAL

    # Seam proof: 1-minimality check
    assert minimizer.verify_1_minimal(trigger_segments, segments) is True


def test_minimizer_unblocked_prompt():
    prompt = "Line 1: Safe\nLine 2: Also safe\nLine 3: Safe"
    segments = LineSegmenter().split(prompt)

    fake_oracle = FakeOracleAdapter(triggers=["UNSEEN_TRIGGER"])
    minimizer = Minimizer(fake_oracle)

    trigger_segments, verdict = minimizer.minimize(segments)

    assert len(trigger_segments) == 0
    assert verdict.blocked is False
    assert verdict.reason_class == ReasonClass.NOT_BLOCKED


def test_minimizer_combination_trigger():
    # Trigger requires both "KEY_A" and "KEY_B" in candidate
    class ComboOracle:
        def test(self, prompt: str):
            from refusal_detector.ports import Verdict
            if "KEY_A" in prompt and "KEY_B" in prompt:
                return Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL)
            return Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)

    lines = [
        "Line 1: Clean",
        "Line 2: Contains KEY_A here",
        "Line 3: Clean middle line",
        "Line 4: Contains KEY_B here",
        "Line 5: Clean end line",
    ]
    segments = LineSegmenter().split("\n".join(lines))

    minimizer = Minimizer(ComboOracle())
    trigger_segments, verdict = minimizer.minimize(segments)

    assert len(trigger_segments) == 2
    indices = {s.index for s in trigger_segments}
    assert indices == {1, 3}
    assert verdict.blocked is True


def test_minimizer_core_segments_analysis():
    class ComboOracle:
        def test(self, prompt: str):
            from refusal_detector.ports import Verdict
            if "KEY_A" in prompt and "KEY_B" in prompt:
                return Verdict(blocked=True, reason_class=ReasonClass.STRUCTURED_REFUSAL)
            return Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)

    lines = [
        "Line 1: Clean",
        "Line 2: Contains KEY_A here",
        "Line 3: Clean middle line",
        "Line 4: Contains KEY_B here",
        "Line 5: Clean end line",
    ]
    segments = LineSegmenter().split("\n".join(lines))
    oracle = ComboOracle()
    minimizer = Minimizer(oracle)

    trigger_segments, _ = minimizer.minimize(segments)
    core = minimizer.compute_core_segments(trigger_segments, segments)

    # In combination trigger, both KEY_A (line index 1) and KEY_B (line index 3) are essential core segments
    assert len(core) == 2
    assert {s.index for s in core} == {1, 3}


def test_mutation_check_fake_oracle_failure():
    """Mutation check: breaking the minimizer logic causes tests to turn RED."""
    lines = [f"Line {i}: Normal content" for i in range(1, 6)]
    lines[2] = "Line 3: TRIGGER_ORIGINAL"
    prompt = "\n".join(lines)
    segments = LineSegmenter().split(prompt)

    oracle = FakeOracleAdapter(triggers=["TRIGGER_ORIGINAL"])
    minimizer = Minimizer(oracle)

    # 1. Normal algorithm (Green)
    trg, verdict = minimizer.minimize(segments)
    assert len(trg) == 1 and trg[0].index == 2

    # 2. Mutated minimizer that drops initial verdict check (Simulating a logic bug -> RED)
    class BrokenMinimizer(Minimizer):
        def _coarse_pass(self, segments, initial_verdict):
            # Broken implementation: returns empty segments
            return [], initial_verdict

    broken = BrokenMinimizer(oracle)
    broken_trg, _ = broken.minimize(segments)
    # The broken implementation fails to isolate the trigger (Red condition verified)
    assert len(broken_trg) != len(trg)


def test_mutation_cycle_full_green_red_green(monkeypatch):
    """Full mutation cycle: green -> break source -> specific test red -> restore -> green."""
    lines = [f"Line {i}: Normal content" for i in range(1, 11)]
    lines[4] = "Line 5: DANGEROUS_WORD payload"
    prompt = "\n".join(lines)
    segments = LineSegmenter().split(prompt)
    oracle = FakeOracleAdapter(triggers=["DANGEROUS_WORD"])

    def assert_isolates() -> None:
        trigger_segments, verdict = Minimizer(oracle).minimize(segments)
        assert len(trigger_segments) == 1
        assert trigger_segments[0].index == 4
        assert verdict.blocked is True

    # 1. GREEN: unmutated source
    assert_isolates()

    # 2. RED: break both minimization passes (no-ops) under a monkeypatch context
    def noop_pass(self, segments, initial_verdict):
        return list(segments), initial_verdict

    with monkeypatch.context() as mp:
        mp.setattr(Minimizer, "_coarse_pass", noop_pass)
        mp.setattr(Minimizer, "_ddmin_pass", noop_pass)

        trigger_segments, _ = Minimizer(oracle).minimize(segments)
        assert len(trigger_segments) == len(segments)  # no minimization occurred
        with pytest.raises(AssertionError):
            assert_isolates()  # the specific assertion goes RED under the mutation

    # 3. GREEN: restored after the context exits
    assert_isolates()

