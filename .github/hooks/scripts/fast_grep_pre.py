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
    "read"
}

DEFAULT_MAPPINGS = [
    {"virtual": "path", "real": ".fast-grep/path", "enabled": True}
]

CONFIG_FILE = ".github/hooks/fast_grep_config.json"
STATE_FILE = ".github/hooks/.fast_grep_link_state.json"
LOG_FILE = ".github/hooks/fast_grep_hook.log"
BACKUP_SUFFIX = ".__fastgrep_backup"


def _normalize_slashes(value: str) -> str:
    """Normalize path separators to forward slash for cross-platform matching.
    
    On Windows, converts backslashes to forward slashes.
    On Unix, forward slash is the native separator, so this is a no-op.
    """
    # Use os.sep to handle both backslash (Windows) and forward slash (Unix)
    return value.replace('\\', '/').replace(os.sep, '/')


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
        "enableSymlinks": True,  # Can be set to False in restricted environments
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


def _ensure_windows_junction(link_path: Path, target_path: Path) -> None:
    """Create a Windows directory junction (hardlink for directories)."""
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _ensure_symlink(link_path: Path, target_path: Path) -> None:
    """Create a Unix symlink."""
    link_path.symlink_to(target_path, target_is_directory=True)


def _is_virtual_path_reference(value: str, cwd: Path, virtual_prefix: str) -> bool:
    normalized = _normalize_slashes(value)
    cwd_normalized = _normalize_slashes(str(cwd)).rstrip("/")
    absolute_virtual = f"{cwd_normalized}/{virtual_prefix}"

    if normalized == virtual_prefix or normalized.startswith(virtual_prefix + "/"):
        return True

    if normalized == absolute_virtual or normalized.startswith(absolute_virtual + "/"):
        return True

    if normalized.endswith("/" + virtual_prefix) or f"/{virtual_prefix}/" in normalized:
        return True

    return False


def _match_virtual_prefix_in_obj(obj, cwd: Path, virtual_prefix: str) -> bool:
    if isinstance(obj, dict):
        return any(_match_virtual_prefix_in_obj(value, cwd, virtual_prefix) for value in obj.values())
    if isinstance(obj, list):
        return any(_match_virtual_prefix_in_obj(item, cwd, virtual_prefix) for item in obj)
    if isinstance(obj, str):
        return _is_virtual_path_reference(obj, cwd, virtual_prefix)
    return False


def _activate_virtual_link(cwd: Path, mapping: dict, enable_symlinks: bool = True) -> str:
    virtual_prefix = mapping["virtual"]
    real_prefix = mapping["real"]
    virtual_path = cwd / virtual_prefix
    real_path = cwd / real_prefix
    backup_path = cwd / f"{virtual_prefix}{BACKUP_SUFFIX}"

    if not real_path.exists() or not real_path.is_dir():
        return "skip:real_missing"

    if not enable_symlinks:
        return "skip:symlinks_disabled"

    state = _load_state(cwd)
    entries = state.setdefault("entries", {})
    entry = entries.setdefault(virtual_prefix, {"count": 0, "backup": str(backup_path)})

    if entry.get("count", 0) > 0:
        entry["count"] = entry.get("count", 0) + 1
        _save_state(cwd, state)
        return f"active:ref+1:{entry['count']}"

    if backup_path.exists():
        return "skip:backup_exists"

    if virtual_path.exists():
        virtual_path.rename(backup_path)

    try:
        if os.name == "nt":
            _ensure_windows_junction(virtual_path, real_path)
        else:
            _ensure_symlink(virtual_path, real_path)
    except Exception as e:
        # Restore backup if link creation failed
        if backup_path.exists():
            backup_path.rename(virtual_path)
        raise RuntimeError(f"Failed to create link: {e}")

    entry["count"] = 1
    _save_state(cwd, state)
    return "active:linked"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    cwd = Path(payload.get("cwd") or os.getcwd())
    config = _load_config(cwd)
    search_tools = set(config.get("searchTools", []))
    mappings = config.get("mappings", [])

    if tool_name not in search_tools:
        _append_log(cwd, "pre", tool_name, "skipped", "tool_not_target")
        print(json.dumps({}))
        return 0

    selected_mapping = None
    for mapping in mappings:
        virtual_prefix = str(mapping.get("virtual", "")).strip()
        _append_log(cwd, "pre-match", tool_name, "checking", f"mapping={mapping!r}; virtual_prefix={virtual_prefix!r}")
        if not virtual_prefix:
            _append_log(cwd, "pre-match", tool_name, "skipped", "empty_virtual_prefix")
            continue
        matched = _match_virtual_prefix_in_obj(tool_input, cwd, virtual_prefix)
        _append_log(
            cwd,
            "pre-match",
            tool_name,
            "result",
            f"virtual_prefix={virtual_prefix!r}; matched={matched}; tool_input={tool_input!r}",
        )
        if matched:
            selected_mapping = mapping
            _append_log(cwd, "pre-match", tool_name, "selected", f"mapping={selected_mapping!r}")
            break

    if selected_mapping is None:
        _append_log(cwd, "pre-match", tool_name, "selected", "none")
        _append_log(cwd, "pre", tool_name, "skipped", "path_not_matched")
        print(json.dumps({}))
        return 0

    enable_symlinks = config.get("enableSymlinks", True)
    link_status = _activate_virtual_link(cwd, selected_mapping, enable_symlinks)
    _append_log(cwd, "pre", tool_name, "handled", f"{selected_mapping['virtual']}->{selected_mapping['real']}:{link_status}")

    response = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": f"fast-grep pre handled for {tool_name}; mapping={selected_mapping['virtual']}->{selected_mapping['real']}; link={link_status}",
        }
    }

    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
