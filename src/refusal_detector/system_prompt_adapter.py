"""A `claude -p` oracle that controls the ambient context instead of inheriting it.

Left to itself the CLI injects its own system prompt and auto-discovers CLAUDE.md from
the working directory, so the same probe gives different answers in different folders.
Holding those flags fixed turns that confound into a constant, and lets pre-prompt
material under test be injected deliberately.
"""

import os
import subprocess
import tempfile

from refusal_detector.classifier import RefusalClassifier
from refusal_detector.cli_auth import check_cli_auth
from refusal_detector.logger import get_logger
from refusal_detector.ports import Verdict

logger = get_logger("system_prompt_adapter")


class SystemPromptCLIAdapter:
    """Probe `claude -p` with an explicit system prompt and fixed ambient context."""

    def __init__(self, timeout: float = 120.0, model: str | None = None) -> None:
        self.timeout = timeout
        self.model = model
        self.classifier = RefusalClassifier()
        self._auth_checked = False

    def test_with_system(self, prompt: str, system_prompt: str = "") -> Verdict:
        """Probe one candidate; raise rather than guess if the CLI cannot answer."""
        if not self._auth_checked:
            check_cli_auth()
            self._auth_checked = True

        system_prompt_path = None
        try:
            cmd = ["claude", "-p", "-", "--exclude-dynamic-system-prompt-sections"]
            if self.model:
                cmd += ["--model", self.model]

            if system_prompt.strip():
                # Pre-prompt material can be many kilobytes; argv would truncate it.
                handle, system_prompt_path = tempfile.mkstemp(
                    prefix="refusal_sysprompt_", suffix=".txt", text=True
                )
                with os.fdopen(handle, "w", encoding="utf-8") as f:
                    f.write(system_prompt)
                cmd += ["--append-system-prompt-file", system_prompt_path]

            try:
                res = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    shell=False,
                )
            except FileNotFoundError as e:
                raise RuntimeError("Claude CLI binary 'claude' not found in PATH.") from e
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"Claude CLI timed out after {self.timeout}s") from e

            output = (res.stdout or "") + "\n" + (res.stderr or "")
            if res.returncode != 0:
                lowered = output.lower()
                if "refus" in lowered or "blocked" in lowered:
                    return self.classifier.classify_moderation_error(output)
                raise RuntimeError(
                    f"Claude CLI exited {res.returncode} without a verdict: {output.strip()[:200]}"
                )
            return self.classifier.classify_text(output)
        finally:
            if system_prompt_path:
                try:
                    os.unlink(system_prompt_path)
                except OSError:
                    logger.warning("Could not remove temp system prompt %s", system_prompt_path)
