import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SEARCH_TOOLS = {
    "list_dir",
    "file_search",
    "grep_search",
    "semantic_search",
    "search",
    "find",
    "ls",
}

DEFAULT_MAPPINGS = [
    {"virtual": "path", "real": ".fast-grep/path", "enabled": True}
]

CONFIG_FILE = ".github/hooks/fast_grep_config.json"
STATE_FILE = ".github/hooks/.fast_grep_link_state.json"
LOG_FILE = ".github/hooks/fast_grep_hook.log"
BACKUP_SUFFIX = ".__fastgrep_backup"


def _load_state(cwd: Path) -> dict:
    state_path = cwd / STATE_FILE
    if not state_path.exists():
        return {"entries": {}}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": {}}


def _save_state(cwd: Path, state: dict) -> None:
    state_path = cwd / STATE_FILE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_config(cwd: Path) -> dict:
    config_path = cwd / CONFIG_FILE
    default_config = {
        "searchTools": sorted(DEFAULT_SEARCH_TOOLS),
        "mappings": DEFAULT_MAPPINGS,
        "enableSymlinks": True,
    }

    if not config_path.exists():
        return default_config

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return default_config

    search_tools = raw.get("searchTools")
    mappings = raw.get("mappings")
    enable_symlinks = raw.get("enableSymlinks", True)

    if not isinstance(search_tools, list):
        search_tools = sorted(DEFAULT_SEARCH_TOOLS)
    if not isinstance(mappings, list):
        mappings = DEFAULT_MAPPINGS
    if not isinstance(enable_symlinks, bool):
        enable_symlinks = True

    normalized_mappings = []
    for item in mappings:
        if not isinstance(item, dict):
            continue
        virtual = str(item.get("virtual", "")).strip()
        real = str(item.get("real", "")).strip()
        enabled = bool(item.get("enabled", True))
        if virtual and real and enabled:
            normalized_mappings.append({"virtual": virtual, "real": real, "enabled": enabled})

    if not normalized_mappings:
        normalized_mappings = DEFAULT_MAPPINGS

    return {
        "searchTools": [str(tool) for tool in search_tools],
        "mappings": normalized_mappings,
        "enableSymlinks": enable_symlinks,
    }


def _append_log(cwd: Path, event: str, tool_name: str, status: str, reason: str) -> None:
    log_path = cwd / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "tool": tool_name,
        "status": status,
        "reason": reason,
    }
    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _deactivate_virtual_link(cwd: Path, mapping: dict) -> str:
    virtual_prefix = mapping["virtual"]
    virtual_path = cwd / virtual_prefix
    backup_path = cwd / f"{virtual_prefix}{BACKUP_SUFFIX}"

    state = _load_state(cwd)
    entries = state.setdefault("entries", {})
    entry = entries.get(virtual_prefix)
    if not entry:
        return "inactive"

    current_count = int(entry.get("count", 0))
    if current_count > 1:
        entry["count"] = current_count - 1
        _save_state(cwd, state)
        return f"active:ref-1:{entry['count']}"

    if virtual_path.exists():
        try:
            if os.name == "nt":
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(virtual_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            elif virtual_path.is_symlink():
                virtual_path.unlink()
            elif virtual_path.is_dir():
                os.rmdir(virtual_path)
        except Exception as e:
            _append_log(cwd, "post", "unknown", "warning", f"Failed to remove link: {e}")

    if backup_path.exists() and not virtual_path.exists():
        backup_path.rename(virtual_path)

    entries.pop(virtual_prefix, None)
    _save_state(cwd, state)
    return "inactive: restored"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name", "")
    cwd = Path(payload.get("cwd") or os.getcwd())
    config = _load_config(cwd)
    search_tools = set(config.get("searchTools", []))
    mappings = config.get("mappings", [])

    if tool_name not in search_tools:
        _append_log(cwd, "post", tool_name, "skipped", "tool_not_target")
        print(json.dumps({}))
        return 0

    statuses = []
    for mapping in mappings:
        virtual_prefix = str(mapping.get("virtual", "")).strip()
        if not virtual_prefix:
            continue
        link_status = _deactivate_virtual_link(cwd, mapping)
        statuses.append(f"{mapping['virtual']}->{mapping['real']}:{link_status}")

    if not statuses:
        statuses.append("none")

    merged_status = " | ".join(statuses)
    _append_log(cwd, "post", tool_name, "handled", merged_status)

    response = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"fast-grep post handled for {tool_name}; {merged_status}",
        },
    }

    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
