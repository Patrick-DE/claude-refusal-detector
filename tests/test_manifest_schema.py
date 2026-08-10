"""Structural validation tests for the plugin's manifest files against Claude Code's real schema."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_json_relies_on_default_discovery_for_hooks_and_mcp():
    plugin = _load_json(REPO_ROOT / ".claude-plugin" / "plugin.json")
    assert "hooks" not in plugin
    assert "mcpServers" not in plugin


def test_no_component_files_nested_under_claude_plugin_dir():
    claude_plugin_dir = REPO_ROOT / ".claude-plugin"
    allowed = {"plugin.json", "marketplace.json"}
    nested_files = {p.name for p in claude_plugin_dir.iterdir() if p.is_file()}
    assert nested_files <= allowed, f"Unexpected files nested in .claude-plugin/: {nested_files - allowed}"


def test_hooks_json_lives_at_default_discovery_path():
    assert (REPO_ROOT / "hooks" / "hooks.json").is_file()


def test_hooks_json_uses_wrapped_plugin_format_with_stop_event():
    hooks_config = _load_json(REPO_ROOT / "hooks" / "hooks.json")
    assert "hooks" in hooks_config, "plugin hooks.json must use the wrapped {description, hooks} format"

    stop_groups = hooks_config["hooks"]["Stop"]
    assert isinstance(stop_groups, list) and stop_groups
    first_group = stop_groups[0]
    assert first_group["matcher"] == "*"
    steps = first_group["hooks"]
    assert steps and steps[0]["type"] == "command"


def test_hooks_json_stop_command_is_portable():
    hooks_config = _load_json(REPO_ROOT / "hooks" / "hooks.json")
    step = hooks_config["hooks"]["Stop"][0]["hooks"][0]
    args = step.get("args", [])
    assert any("${CLAUDE_PLUGIN_ROOT}" in a for a in args), "Stop hook must locate its script via ${CLAUDE_PLUGIN_ROOT}"


def test_marketplace_json_owner_is_an_object_with_a_name():
    marketplace = _load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    assert isinstance(marketplace["owner"], dict)
    assert marketplace["owner"]["name"]


def test_marketplace_json_description_is_top_level_not_nested_in_metadata():
    marketplace = _load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    assert "description" in marketplace
    assert "metadata" not in marketplace


def test_marketplace_json_self_hosted_plugin_source_points_at_repo_root():
    marketplace = _load_json(REPO_ROOT / ".claude-plugin" / "marketplace.json")
    plugin_entry = marketplace["plugins"][0]
    assert plugin_entry["source"] == "./"
    assert "path" not in plugin_entry
