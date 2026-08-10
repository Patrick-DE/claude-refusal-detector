"""RefusalDetector application service orchestrating segmenting, cached oracle execution, and reporting."""

import difflib
from typing import Any

from refusal_detector.config import (
    Config,
    build_cache,
    build_oracle,
    build_reporter,
    build_segmenter,
)
from refusal_detector.input_loader import load_text_from_file_or_string
from refusal_detector.logger import get_logger
from refusal_detector.minimizer import Minimizer
from refusal_detector.ports import (
    DetectionReport,
    Oracle,
    ReasonClass,
    Segment,
    Verdict,
)
from refusal_detector.runner import CachedOracle

logger = get_logger("service")


class RefusalDetector:
    """Core application service providing prompt diagnosis and checking."""

    def __init__(self, oracle: Oracle | None = None, config: Config | None = None) -> None:
        self.config = config or Config.from_env()
        self._injected_oracle = oracle

    def check(self, prompt_or_path: str, config: Config | None = None) -> Verdict:
        """Check if a prompt string or file is currently blocked (no minimization)."""
        cfg = config or self.config
        text = load_text_from_file_or_string(prompt_or_path)
        oracle = self._get_oracle(cfg)
        logger.info("Executing check on %d characters of text", len(text))
        return oracle.test(text)

    def detect(self, prompt_or_path: str, config: Config | None = None) -> DetectionReport:
        """Analyze prompt or file to pinpoint the 1-minimal refusal trigger."""
        cfg = config or self.config
        text = load_text_from_file_or_string(prompt_or_path)
        segmenter = build_segmenter(cfg.split_mode)
        segments = segmenter.split(text)

        logger.info("Starting detection: %d chars, %d segments (%s split)", len(text), len(segments), cfg.split_mode)

        cached_oracle = self._get_oracle(cfg)
        minimizer = Minimizer(cached_oracle)

        trigger_segments, final_verdict = minimizer.minimize(segments)

        trigger_text = "".join(s.text for s in trigger_segments)
        non_trigger_text = "".join(s.text for s in segments if s not in trigger_segments)

        # Check if removing trigger unblocks prompt
        is_necessary = False
        if trigger_segments:
            unblock_verdict = cached_oracle.test(non_trigger_text)
            is_necessary = not unblock_verdict.blocked
            logger.info("Trigger removal verification: unblocked=%s", is_necessary)

        core_segments = minimizer.compute_core_segments(trigger_segments, segments)

        diff_text = self._format_diff(segments, trigger_segments)

        total_calls = getattr(cached_oracle, "total_calls", 0)
        cache_hits = getattr(cached_oracle, "cache_hits", 0)

        repro_payload: dict[str, Any] = {
            "provider": cfg.provider,
            "model": cfg.model,
            "split_mode": cfg.split_mode,
            "total_segments": len(segments),
            "trigger_segment_indices": [s.index for s in trigger_segments],
            "core_segment_indices": [s.index for s in core_segments],
            "reason_class": final_verdict.reason_class.value,
        }

        report = DetectionReport(
            original_prompt=text,
            segments=segments,
            trigger_segments=trigger_segments,
            trigger_text=trigger_text,
            diff_text=diff_text,
            reason_class=final_verdict.reason_class,
            total_calls=total_calls,
            cache_hits=cache_hits,
            is_necessary=is_necessary,
            core_segments=core_segments,
            repro_payload=repro_payload,
        )

        logger.info("Detection finished. Isolated %d trigger segments (%d core).", len(trigger_segments), len(core_segments))
        return report


    def render_report(self, report: DetectionReport) -> str:
        """Render DetectionReport to Markdown using configured reporter."""
        reporter = build_reporter()
        return reporter.render(report)

    def _get_oracle(self, cfg: Config) -> CachedOracle | Oracle:
        if self._injected_oracle is not None:
            if isinstance(self._injected_oracle, CachedOracle):
                return self._injected_oracle
            cache = build_cache(cfg.cache_file_path)
            return CachedOracle(self._injected_oracle, cache, max_calls=cfg.max_calls)

        base_oracle = build_oracle(cfg)
        cache = build_cache(cfg.cache_file_path)
        return CachedOracle(base_oracle, cache, max_calls=cfg.max_calls)

    def _format_diff(self, all_segments: list[Segment], trigger_segments: list[Segment]) -> str:
        trg_set = set(trigger_segments)
        diff_lines = []
        for s in all_segments:
            prefix = "- " if s in trg_set else "  "
            # Format lines nicely
            clean_lines = s.text.rstrip("\r\n").split("\n")
            for line in clean_lines:
                diff_lines.append(f"{prefix}{line}")
        return "\n".join(diff_lines)
