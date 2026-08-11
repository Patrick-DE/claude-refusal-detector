"""Concrete adapters for Cache, Reporter, and Provider Oracles."""

import json
import os
import shutil
import subprocess
from typing import Any

import httpx

from refusal_detector.classifier import RefusalClassifier
from refusal_detector.logger import get_logger
from refusal_detector.ports import (
    Cache,
    DetectionReport,
    Oracle,
    ReasonClass,
    Reporter,
    Verdict,
)

logger = get_logger("adapters")


class InMemoryCache(Cache):
    """Simple in-memory dictionary cache."""

    def __init__(self) -> None:
        self._store: dict[str, Verdict] = {}

    def get(self, prompt_hash: str) -> Verdict | None:
        return self._store.get(prompt_hash)

    def set(self, prompt_hash: str, verdict: Verdict) -> None:
        self._store[prompt_hash] = verdict


class JsonFileCache(Cache):
    """Persistent JSON file cache."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self._store: dict[str, Verdict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self._store[k] = Verdict(
                            blocked=v["blocked"],
                            reason_class=ReasonClass(v["reason_class"]),
                            details=v.get("details", ""),
                        )
            except Exception as e:
                logger.warning("Could not load cache file %s: %s", self.file_path, e)

    def _save(self) -> None:
        try:
            data = {
                k: {
                    "blocked": v.blocked,
                    "reason_class": v.reason_class.value,
                    "details": v.details,
                }
                for k, v in self._store.items()
            }
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Could not save cache file %s: %s", self.file_path, e)

    def get(self, prompt_hash: str) -> Verdict | None:
        return self._store.get(prompt_hash)

    def set(self, prompt_hash: str, verdict: Verdict) -> None:
        self._store[prompt_hash] = verdict
        self._save()


class FakeOracleAdapter(Oracle):
    """Test oracle adapter matching against known trigger strings."""

    def __init__(self, triggers: list[str], reason_class: ReasonClass = ReasonClass.STRUCTURED_REFUSAL) -> None:
        self.triggers = triggers
        self.reason_class = reason_class

    def test(self, prompt: str) -> Verdict:
        for trg in self.triggers:
            if trg in prompt:
                return Verdict(
                    blocked=True,
                    reason_class=self.reason_class,
                    details=f"Matched known test trigger '{trg}'",
                )
        return Verdict(blocked=False, reason_class=ReasonClass.NOT_BLOCKED)


class MarkdownReporter(Reporter):
    """Renders DetectionReport to structured Markdown."""

    def render(self, report: DetectionReport) -> str:
        lines = []
        lines.append("# Claude Refusal Detector — Diagnostic Report")
        lines.append("")

        if not report.trigger_segments:
            lines.append("### Status: No Refusal Trigger Found")
            lines.append("The input prompt was not blocked by guardrails or no minimal trigger subset could be isolated.")
            lines.append(f"- **Total Calls Made:** {report.total_calls}")
            lines.append(f"- **Cache Hits:** {report.cache_hits}")
            return "\n".join(lines)

        lines.append(f"### Status: Trigger Isolated ({report.reason_class.value})")
        lines.append(f"- **Reason Class:** `{report.reason_class.value}`")
        lines.append(f"- **Verified Necessary:** `{'Yes' if report.is_necessary else 'No'}`")
        lines.append(f"- **API Calls:** {report.total_calls} (Cache Hits: {report.cache_hits})")
        lines.append("")

        lines.append("## Minimal Trigger Content")
        lines.append("```text")
        lines.append(report.trigger_text.strip())
        lines.append("```")
        lines.append("")

        if report.core_segments:
            lines.append("## Essential Core Segments")
            lines.append("The following segment(s) are essential root causes required for the refusal:")
            for core in report.core_segments:
                lines.append(f"- Segment #{core.index} (Lines {core.start_line}-{core.end_line}): `{core.text.strip()}`")
            lines.append("")

        lines.append("## Trigger Positions")
        lines.append("| Segment Index | Line Range | Character Range | Content Snippet |")
        lines.append("|---|---|---|---|")
        for seg in report.trigger_segments:
            snippet = seg.text.strip().replace("\n", "\\n")
            if len(snippet) > 40:
                snippet = snippet[:37] + "..."
            lines.append(
                f"| {seg.index} | Lines {seg.start_line}-{seg.end_line} | {seg.start_char}-{seg.end_char} | `{snippet}` |"
            )
        lines.append("")

        lines.append("## Prompt Diff (Prompt without Trigger)")
        lines.append("```diff")
        lines.append(report.diff_text)
        lines.append("```")
        lines.append("")

        lines.append("## Repro Payload")
        lines.append("```json")
        lines.append(json.dumps(report.repro_payload, indent=2))
        lines.append("```")

        return "\n".join(lines)


class ClaudeCodeCLIAdapter(Oracle):
    """Keyless Oracle calling local `claude -p` CLI with shell-injection safety."""

    def __init__(self, timeout: float = 30.0, model: str | None = None) -> None:
        self.timeout = timeout
        self.model = model
        self.classifier = RefusalClassifier()

    def test(self, prompt: str) -> Verdict:

        # Guardrails are model-specific: probing a different model than the one that
        # refused reports "not blocked" for a prompt that really was blocked.
        cmd = ["claude", "-p", "-"]
        if self.model:
            cmd += ["--model", self.model]
        try:
            # shell=False to prevent shell command injection on Windows/POSIX
            res = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
            )
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            if res.returncode != 0:
                lowered = output.lower()
                if "refus" in lowered or "blocked" in lowered:
                    return self.classifier.classify_moderation_error(output)
                # Fail loud: a CLI that could not run tells us nothing about the
                # prompt. Treating this as "not blocked" produced false negatives
                # in the field for every diagnosis made while logged out.
                raise RuntimeError(
                    f"Claude CLI exited {res.returncode} without a verdict: {output.strip()[:200]}"
                )
            return self.classifier.classify_text(output)
        except FileNotFoundError:
            raise RuntimeError("Claude CLI binary 'claude' not found in PATH.")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Claude CLI execution timed out after {self.timeout}s")
        except RuntimeError:
            # Already a deliberate, well-formed failure - re-wrapping would bury the message.
            raise
        except Exception as e:
            raise RuntimeError(f"Claude CLI execution failed: {e}")


class AnthropicAPIAdapter(Oracle):
    """Oracle adapter calling Anthropic Messages API with strict fail-loud error handling."""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022", timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.classifier = RefusalClassifier()

    def test(self, prompt: str) -> Verdict:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        with httpx.Client(timeout=self.timeout) as client:
            res = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            if res.status_code in (400, 422):
                err_text = res.text.lower()
                if any(kw in err_text for kw in ("safety", "policy", "moderation", "refus", "blocked")):
                    return self.classifier.classify_moderation_error(res.text)
            res.raise_for_status()
            data = res.json()
            return self.classifier.classify_anthropic_response(data)


class OpenAIAdapter(Oracle):
    """Oracle adapter for OpenAI-compatible endpoints (DeepSeek direct, OpenRouter) with fail-loud error handling."""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.classifier = RefusalClassifier()

    def test(self, prompt: str) -> Verdict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(url, headers=headers, json=payload)
            if res.status_code in (400, 422):
                err_text = res.text.lower()
                if any(kw in err_text for kw in ("safety", "policy", "moderation", "content_filter", "refus")):
                    return self.classifier.classify_moderation_error(res.text)
            res.raise_for_status()
            data = res.json()
            return self.classifier.classify_openai_response(data)
