"""Pure delta debugging (ddmin) + coarse half-split minimizer."""

from refusal_detector.logger import get_logger
from refusal_detector.ports import Oracle, ReasonClass, Segment, Verdict

logger = get_logger("minimizer")


def _join_segments(segments: list[Segment]) -> str:
    """Reconstruct prompt text from a list of segments."""
    return "".join(s.text for s in segments)


def _chunk_list(lst: list[Segment], n: int) -> list[list[Segment]]:
    """Divide a list into n roughly equal contiguous sublists."""
    k, m = divmod(len(lst), n)
    return [lst[i * k + min(i, m) : (i + 1) * k + min(i + 1, m)] for i in range(n)]


class Minimizer:
    """Delta debugging minimizer implementing half-split coarse pass and ddmin."""

    def __init__(self, oracle: Oracle) -> None:
        self.oracle = oracle

    def minimize(self, segments: list[Segment]) -> tuple[list[Segment], Verdict]:
        """Find a 1-minimal subset of segments that triggers a refusal.

        Returns (trigger_segments, final_verdict). If initial prompt is not blocked,
        returns ([], Verdict(blocked=False)).
        """
        if not segments:
            return [], Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)

        full_prompt = _join_segments(segments)
        initial_verdict = self.oracle.test(full_prompt)
        if not initial_verdict.blocked:
            logger.info("Initial prompt is NOT blocked. No trigger to minimize.")
            return [], initial_verdict

        logger.info(
            "Initial prompt IS blocked (%d segments). Starting coarse minimization phase.",
            len(segments),
        )
        last_blocked_verdict = initial_verdict

        # Phase A: Coarse Half-Split Pass
        current_segments, last_blocked_verdict = self._coarse_pass(segments, last_blocked_verdict)
        logger.info("Coarse phase completed. Reduced to %d segments. Starting ddmin.", len(current_segments))

        # Phase B: Fine ddmin Pass
        current_segments, last_blocked_verdict = self._ddmin_pass(current_segments, last_blocked_verdict)
        logger.info("ddmin completed. Minimal trigger set contains %d segments.", len(current_segments))

        return current_segments, last_blocked_verdict

    def _coarse_pass(
        self, segments: list[Segment], initial_verdict: Verdict
    ) -> tuple[list[Segment], Verdict]:
        """Phase A: Repeatedly test halves to drop large unneeded sections quickly."""
        curr = list(segments)
        last_verdict = initial_verdict

        n = 2
        while len(curr) >= 2:
            chunks = _chunk_list(curr, n)
            reduced = False
            for chunk in chunks:
                candidate = [s for s in curr if s not in chunk]
                if not candidate:
                    continue
                v = self.oracle.test(_join_segments(candidate))
                if v.blocked:
                    logger.debug("Coarse pass dropped chunk of size %d. Segments left: %d", len(chunk), len(candidate))
                    curr = candidate
                    last_verdict = v
                    n = max(n - 1, 2)
                    reduced = True
                    break
            if not reduced:
                if n >= len(curr):
                    break
                n = min(len(curr), n * 2)

        return curr, last_verdict

    def _ddmin_pass(
        self, segments: list[Segment], initial_verdict: Verdict
    ) -> tuple[list[Segment], Verdict]:
        """Phase B: Zeller's ddmin algorithm for 1-minimal trigger recovery."""
        curr = list(segments)
        last_verdict = initial_verdict

        if len(curr) <= 1:
            return curr, last_verdict

        n = 2
        while len(curr) >= 2:
            chunks = _chunk_list(curr, n)
            reduced = False

            # 1. Test complement of each chunk
            for chunk in chunks:
                candidate = [s for s in curr if s not in chunk]
                if not candidate:
                    continue
                v = self.oracle.test(_join_segments(candidate))
                if v.blocked:
                    logger.debug("ddmin complement test: reduced %d -> %d segments", len(curr), len(candidate))
                    curr = candidate
                    last_verdict = v
                    n = max(n - 1, 2)
                    reduced = True
                    break

            if reduced:
                continue

            # 2. Test each chunk directly
            for chunk in chunks:
                if len(chunk) == len(curr):
                    continue
                v = self.oracle.test(_join_segments(chunk))
                if v.blocked:
                    logger.debug("ddmin chunk test: reduced %d -> %d segments", len(curr), len(chunk))
                    curr = chunk
                    last_verdict = v
                    n = 2
                    reduced = True
                    break

            if not reduced:
                if n >= len(curr):
                    break
                n = min(len(curr), n * 2)

        return curr, last_verdict

    def verify_1_minimal(self, trigger_segments: list[Segment], full_segments: list[Segment]) -> bool:
        """Verify that removing any single segment from trigger_segments unblocks the prompt."""
        if not trigger_segments:
            return True

        non_trigger = [s for s in full_segments if s not in trigger_segments]
        for seg in trigger_segments:
            candidate = non_trigger + [s for s in trigger_segments if s != seg]
            candidate.sort(key=lambda s: s.index)
            v = self.oracle.test(_join_segments(candidate))
            if v.blocked:
                logger.warning("1-minimality check failed: removing segment %d still blocked!", seg.index)
                return False
        return True

    def compute_core_segments(self, trigger_segments: list[Segment], full_segments: list[Segment]) -> list[Segment]:
        """Compute essential core segments (segments in trigger_segments whose removal unblocks the prompt)."""
        if not trigger_segments:
            return []

        core = []
        non_trigger = [s for s in full_segments if s not in trigger_segments]
        for seg in trigger_segments:
            candidate = non_trigger + [s for s in trigger_segments if s != seg]
            candidate.sort(key=lambda s: s.index)
            v = self.oracle.test(_join_segments(candidate))
            if not v.blocked:
                core.append(seg)
        return core

