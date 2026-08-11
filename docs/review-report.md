# Implementation Review Report

**Date:** 2026-08-10 (initial), **Re-check:** 2026-08-10 (fixes verified)
**Reviewed against:** `docs/design.md`, `docs/plan.md`, `docs/vision.md`
**Verdict (initial):** Good architecture, tests green (23 passed / 2 skipped by design), but **2 issues must be fixed before use** (one security, one correctness). Several moderate/minor gaps also listed.
**Verdict (re-check):** **All findings closed** — release blockers, plugin layer, and minor items
(M4/M6/M8) fixed and verified.

## Executive summary

The implementation follows the designed ports & adapters architecture faithfully: thin CLI + MCP
entry points over a single `RefusalDetector` service, pure minimizer/classifier, `CachedOracle`
wrapper, config factories, per-session cache, Markdown reporter, fail-loud runner. Module layout
matches the design. Static checks are clean; the fake-oracle seam proof and 1-minimality
verification are present and pass.

Two findings are release-blocking because they produce **wrong results or unsafe execution**:

1. **Command injection** in `ClaudeCodeCLIAdapter` (the keyless plugin-path oracle).
2. **Fail-loud violation** in both HTTP adapters — auth/server errors are silently reported as
   "blocked" refusals.

## Re-check (2026-08-10) — fixes verified

| Finding | Status | Evidence |
|---|---|---|
| C1 command injection | ✅ **Fixed** | `shell=False` + prompt via stdin (`claude -p -`); `test_c1_claude_cli_shell_injection_safety` asserts `shell=False` + stdin |
| C2 fail-loud | ✅ **Fixed** | 400/422 → moderation only when body has safety keywords; 401/403/5xx raise; `test_c2_*_fail_loud_on_401_and_500` |
| M1 core analysis | ✅ **Fixed** | `compute_core_segments` in `minimizer.py`, wired in `service.detect`, rendered by `MarkdownReporter`, in repro payload; `test_minimizer_core_segments_analysis` |
| M2 real round trip | ✅ **Fixed** | `test_known_trigger_detect_unblock_roundtrip`: detect → exact segment → removal un-blocks → report assertions (fake oracle); live-API tests stay key-gated |
| M3 MCP file path | ✅ **Fixed** | MCP tool accepts `prompt_or_path` |
| M5 dead ternary | ✅ **Fixed** | removed in `service.py` |
| M7 CLI preflight | ✅ **Fixed** | `FileNotFoundError` → clear `RuntimeError` |
| M9 plugin packaging | ✅ **Fixed** | `.claude-plugin/plugin.json` + `hooks.json` (PostInvocation), `hooks/refusal_hook.py`, `test_plugin_hook.py`, README install docs |
| M4 real mutation cycle | ✅ **Fixed** | `test_mutation_cycle_full_green_red_green`: green → passes mutated to no-ops → specific assertion red → restore → green |
| M6 exit-code semantics | ✅ **Fixed** | exit codes documented in `cli.py` epilog + README; `test_cli_exit_codes` |
| M8 retries vs budget | ✅ **Fixed** | documented in `runner.py` docstring + README + design §4.4 |

**Suite:** 30 passed, 2 skipped (integration, key-gated by design) · no static errors.

---

## Verified positives

- [x] Ports & adapters structure (`ports.py`, `adapters.py`, `service.py`, thin `cli.py`/`desktop_plugin.py`).
- [x] `CachedOracle` implements the `Oracle` port; cache + budget + retries + fail-loud in one place.
- [x] Per-provider classification (Anthropic `stop_reason`/`refusal` block; OpenAI `content_filter`/`refusal`).
- [x] `verify_1_minimal` seam proof; fake-oracle minimizer tests; service test with fakes (no network).
- [x] Config factories (`build_oracle`/`build_cache`/`build_reporter`/`build_segmenter`); keys from env, never logged/committed.
- [x] `pytest`: **23 passed, 2 skipped** (integration skipped without API keys, per design); no static errors.

---

## CRITICAL findings (must fix)

### C1. Command injection in `ClaudeCodeCLIAdapter` (`adapters.py`)

**Where:** `ClaudeCodeCLIAdapter.test()` → `subprocess.run(..., shell=True if os.name == "nt" else False)`.

**Problem:** On Windows the adapter joins `["claude", "-p", prompt]` and runs it through `cmd.exe`.
The `prompt` is **untrusted input** (it is the very content being scanned). Shell metacharacters in
the prompt (`&`, `|`, `>`, `^`, …) are interpreted — an attacker-controlled prompt can execute
arbitrary commands on the host.

**Evidence (harmless repro, shell=True + `echo`):**
```
input args:  ['echo', 'hello', '&', 'echo', 'INJECTED_BY_SHELL']
output:      hello
             INJECTED_BY_SHELL        <- second command executed
```

**Fix:** Use `shell=False` on **all** platforms (the list form is passed safely to the process
without a shell), or pass the prompt via stdin (`claude -p` reads stdin when no prompt arg is
given). Add a test asserting `shell=False`/stdin usage.

---

### C2. Fail-loud violation: HTTP errors reported as refusals (`adapters.py`)

**Where:** `AnthropicAPIAdapter.test()` and `OpenAIAdapter.test()`.

**Problem:** Any HTTP error other than 429/529 (e.g. **401 bad key, 403, 500, 503**) is caught and
converted to `Verdict(blocked=True, reason_class=MODERATION_ERROR)`. An auth failure or server
error therefore makes a benign prompt look "blocked", and the minimizer will hunt for — and
report — a trigger that does not exist. This contradicts design §4.4 ("a failed test is never
silently treated as 'not blocked'") and silently breaks every diagnosis until the real API issue
is noticed.

**Evidence (mocked 401):**
```
AnthropicAPIAdapter.test('totally benign text') -> blocked=True reason=moderation_error
OpenAIAdapter.test('totally benign text')       -> blocked=True reason=moderation_error
```

**Fix:** Only treat the documented input-guardrail rejections (**400/422**) as moderation blocks.
Raise for auth errors (401/403) and all 5xx. Likewise in `classify_openai_response`, an
unexpected/empty `choices` shape should raise, not default to `NOT_BLOCKED`. Add unit tests:
401 → raises; 500 → raises; 400/422 → `MODERATION_ERROR`.

---

## MODERATE findings (should fix)

### M1. Missing design feature: "core analysis"
Design §4.3 specifies a multi-run analysis flagging segments present in *every* minimal trigger
(useful for combination triggers). Not implemented. Add `core_segments` to `DetectionReport` and
run the minimizer multiple times (or track across ddmin passes).

### M2. Integration tests do not meet the design's real-round-trip criterion
Design §9 / plan P2.3 requires: a **known-trigger fixture + real endpoint**, assert `detect`
names the exact segment and removal un-blocks. Current `test_integration.py` only runs `check`
on a benign prompt (and skips without keys). Add a real `detect` round trip with a known trigger
against the configured endpoint, plus an OpenRouter case; keep it skipped-without-key, but make
it exercise the full pipeline.

### M3. MCP tool does not accept a file path
Design §4.8: "blocked prompt (or a file path)". `detect_refusal_trigger` only takes a `prompt`
string. Accept an optional `file_path` argument (reuse `load_text_from_file_or_string`).

### M9. No Claude plugin packaging (the primary surface is missing)
The plugin is the user's **primary** integration surface, but the repo only ships the raw MCP
server: no `.claude-plugin/plugin.json` manifest, no refusal-detection **hooks** (needed for the
vision's automatic trigger — an MCP tool alone cannot fire on a refusal), and no
install/registration instructions. Add the plugin layer; keep the MCP server as its engine.

---

## MINOR findings (nice to have)

- **M4.** `test_mutation_check_fake_oracle_failure` is a negative test, not the mutation check
  promised by plan P3.3 (break source → specific test goes red → restore → green). Perform the
  real mutation cycle once on the minimizer test.
- **M5.** `service.py` dead ternary: `final_verdict if final_verdict else ...` — `Verdict` is a
  dataclass and always truthy; simplify.
- **M6.** Exit codes: `check` returns 1 when blocked, `detect` always returns 0. Document the
  semantics or make them consistent.
- **M7.** `ClaudeCodeCLIAdapter` has no preflight check that the `claude` CLI exists; fail early
  with a clear message instead of a generic `RuntimeError`.
- **M8.** Retried calls do not count toward `max_calls` budget. Fine, but document it.

---

## Suggested fix priority

1. **C1 + C2** — release blockers; both are small, localized changes in `adapters.py`.
2. **M1, M2** — close the design gap and the verification gap before real API usage.
3. **M3–M8** — quality pass; can ride along with the fix PR.

**Files to touch for the blockers:** `src/refusal_detector/adapters.py` (+ `tests/test_adapters.py` or
extend `tests/test_runner.py`).

---

## Plugin-layer re-check (2026-08-10) — `plugin-dev` validation

M9 ("plugin packaging") was marked fixed above on 2026-08-10, but that check never verified the
plugin against Claude Code's actual hook/manifest contract — it only confirmed the files existed.
A `plugin-dev:plugin-validator` pass the same day found the plugin was a **no-op**: the hook lived
in a disallowed location and pointed at a non-existent event (`PostInvocation`), its payload/output
schema didn't match any real Claude Code hook contract, and `marketplace.json` had three schema
deviations that would have broken the documented install command.

| Finding | Status | Evidence |
|---|---|---|
| Hook unreachable (`.claude-plugin/hooks.json`, no `./` prefix, wrong dir) | Fixed | Moved to `hooks/hooks.json`; `plugin.json` relies on default discovery; `test_hooks_json_lives_at_default_discovery_path` |
| Invalid hooks.json schema (`PostInvocation`, missing `matcher`/`hooks` wrapper) | Fixed | Rewritten as `Stop` event, wrapped format; `test_hooks_json_uses_wrapped_plugin_format_with_stop_event` |
| Hook payload/output used invented fields | Fixed | Rewritten against the real `transcript_path`/`systemMessage` contract; `test_plugin_hook.py` |
| `marketplace.json` schema (owner string, nested description, wrong source) | Fixed | `test_marketplace_json_*` in `test_manifest_schema.py` |
| MCP server never registered | Fixed | `.mcp.json`; `test_mcp_json_registers_the_desktop_plugin_server` |
| Non-portable hook command, no LICENSE, tracked bytecode | Fixed | `${CLAUDE_PLUGIN_ROOT}`-relative invocation + stdlib sys.path bootstrap; `.gitignore`; `LICENSE` |
| Hook interpreter regression (`python3` doesn't resolve on this machine) + auto-trigger safety gaps | Fixed | Final whole-branch review caught `python3` failing empirically (Windows Store stub); `hooks/hooks.json` now uses `python` + 120s timeout; hook adds a reentrancy env-var guard and a capped `max_calls=10`; README documents the `pip install -e .` prerequisite |

**Suite:** 44 passed, 2 skipped (confirmed after the final whole-branch review's fix wave).

---

## Field incident (2026-08-11) — orphaned hook processes, fixed in 0.2.1

Reported from a live session shortly after 0.2.0 shipped. Three `refusal_hook.py` processes
were found running concurrently, the oldest for ~18 minutes, despite the hook's 120s timeout.

**Root cause:** `main()` called `sys.stdin.read()`, which blocks forever when stdin never
reaches EOF. Claude Code stops *waiting* at the hook timeout, but does not guarantee the
process dies — so each affected Stop event leaked one blocked interpreter (~62 MB), and they
accumulated. The bug predates 0.2.0 (the original hook had the same `main()`), but was
unreachable until 0.2.0 made the hook actually fire.

**Not affected:** no API quota was consumed. The processes blocked *before* the classifier,
so detection never ran and no `claude -p` probes were spawned. Scanning every assistant
message across two hours of transcripts produced zero refusal classifications, confirming
the leak was independent of refusal content.

| Fix | Evidence |
|---|---|
| `read_hook_payload()` reads on a daemon thread with a 10s timeout, and short-circuits on a tty | `test_read_hook_payload_gives_up_when_stdin_never_reaches_eof`, `test_read_hook_payload_returns_piped_input` |
| Wall-clock watchdog (110s, under the hook's 120s) hard-exits rather than running detached | `_start_wall_clock_watchdog` |
| Process-level regression test holding stdin open | `test_hook_process_exits_when_stdin_is_never_closed` — uses `proc.wait()`, never `communicate()`, which would close stdin and pass regardless |

Both new tests were falsified green→red→green by reverting `reader.join(timeout)` to a
blocking `reader.join()`.

**Suite:** 47 passed, 2 skipped.

**Known limitation (unfixed):** the text-pattern classifier is broad. Measured against eight
realistic phrasings, five classified as refusals, including benign ones (declining to proceed
pending input; quoting a linter about a policy rule). Each true match spawns up to
`_HOOK_MAX_CALLS` (10) `claude -p` probes, so false positives cost real quota and session
latency. Consider narrowing the patterns or gating the hook behind a flag file before daily use.

---

## Field incident (2026-08-11) — the hook could not see real refusals, fixed in 0.3.0

Reported directly: a refusal warning appeared in a session and the plugin returned nothing.

**Root cause:** Claude Code does not express an API-level refusal as assistant prose. It
writes a `system` record with `subtype: "model_refusal_fallback"`, carrying
`apiRefusalCategory`, `apiRefusalExplanation`, and the `originalModel`/`fallbackModel` pair.
The hook only scanned assistant `text` blocks and pattern-matched them, so it saw nothing.
In the reported session all eight assistant records held only `thinking` and `tool_use`
blocks — zero text — so the hook bailed at its own `not assistant_reply` guard.

The plugin was therefore blind to precisely the event it exists to diagnose, while remaining
sensitive to conversational phrasing that merely resembles a refusal.

A second, compounding gap: the prompt that triggered the refusal carried attachments, so its
`message.content` was a block list rather than a string, and the extractor accepted only
strings.

| Fix | Evidence |
|---|---|
| `_find_structured_refusal` treats the `model_refusal_fallback` record as authoritative, checked before any pattern matching | `test_structured_api_refusal_is_detected_without_any_assistant_text` |
| `_message_text` reads prose from block-list content, still excluding `tool_result`/`tool_use`/`thinking` | `test_structured_refusal_reads_prompt_that_carries_attachments` |
| The report states which signal fired, so a high-confidence structured hit is distinguishable from a heuristic one | banner asserted in `test_process_hook_payload_refusal_auto_trigger` |
| `isMeta` records skipped when resolving the prompt | `_last_user_prompt_before` |

Verified against the two real transcripts that prompted the report: both now fire, both
report category `cyber`, and both resolve the correct originating prompt. Tests falsified
green→red→green by stubbing `_find_structured_refusal` to return `None`.

**Suite:** 50 passed, 2 skipped.

**Note on the reported case:** both refusals were on ordinary code-audit requests, classified
`cyber` — false positives from the model's safeguards, which is the exact scenario this tool
was built to diagnose.
