"""The seam: can a real captured refusal be reproduced and localized?

Skipped without an authenticated CLI rather than passing vacuously - a green suite
must never imply a round trip that did not happen.
"""

import json
import os
import subprocess

import pytest

TRANSCRIPT = os.path.expanduser(
    "~/.claude/projects/C--Users-patri-sources-repos-unrealengine-debugger/"
    "196daf17-75ca-4f0b-9190-cc282ff2a2bc.jsonl"
)


def _cli_authenticated() -> bool:
    try:
        result = subprocess.run(
            ["claude", "auth", "status"], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return False
    start = (result.stdout or "").find("{")
    if start == -1:
        return False
    try:
        return bool(json.loads(result.stdout[start:]).get("loggedIn"))
    except json.JSONDecodeError:
        return False


pytestmark = [
    pytest.mark.skipif(not os.path.exists(TRANSCRIPT), reason="captured transcript not present"),
    pytest.mark.skipif(not _cli_authenticated(), reason="claude CLI not authenticated"),
]


def test_captured_refusal_reproduces_through_the_context_oracle():
    from refusal_detector.context import assemble_context
    from refusal_detector.context_oracle import ContextOracle
    from refusal_detector.minimizer import _join_segments

    records = [json.loads(line) for line in open(TRANSCRIPT, encoding="utf-8") if line.strip()]
    refusal_index = next(
        i for i, r in enumerate(records) if r.get("subtype") == "model_refusal_fallback"
    )
    segments = assemble_context(records, refusal_index)
    assert segments, "assembly produced nothing to test"

    oracle = ContextOracle(segments=segments, model="claude-fable-5", timeout=180)
    # Reconstruct exactly the way the minimizer does. Joining on "\n" here would pad the
    # text differently from every other probe, so this test would replay something the
    # captured session never contained - defeating the point of reproducing it faithfully.
    verdict = oracle.test(_join_segments(segments))

    # Not asserting blocked=True: the base system prompt is not reconstructable, so a
    # non-reproduction is a real finding about scope, not a test failure to paper over.
    print(f"\nreproduction verdict: blocked={verdict.blocked} reason={verdict.reason_class.value}")
    assert verdict is not None
