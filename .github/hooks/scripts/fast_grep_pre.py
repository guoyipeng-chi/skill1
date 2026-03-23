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
BUILD_SCRIPT = ".github/hooks/scripts/build_fast_grep_links.py"
DEFAULT_LIST_FILE = ".github/hooks/fast_grep_items.txt"


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
        "autoRefreshBeforePre": {
            "enabled": True,
            "script": BUILD_SCRIPT,
            "listFile": DEFAULT_LIST_FILE,
            "clean": True,
            "useMappingsAsDefaultItems": True,
        },
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
    auto_refresh_before_pre = raw.get("autoRefreshBeforePre", default_config["autoRefreshBeforePre"])

    if not isinstance(search_tools, list):
        search_tools = sorted(DEFAULT_SEARCH_TOOLS)
    if not isinstance(mappings, list):
        mappings = DEFAULT_MAPPINGS
    if not isinstance(enable_symlinks, bool):
        enable_symlinks = True
    if not isinstance(auto_refresh_before_pre, dict):
        auto_refresh_before_pre = default_config["autoRefreshBeforePre"]

    refresh_enabled = bool(auto_refresh_before_pre.get("enabled", True))
    refresh_script = str(auto_refresh_before_pre.get("script", BUILD_SCRIPT)).strip() or BUILD_SCRIPT
    refresh_list_file = str(auto_refresh_before_pre.get("listFile", DEFAULT_LIST_FILE)).strip() or DEFAULT_LIST_FILE
    refresh_clean = bool(auto_refresh_before_pre.get("clean", True))
    refresh_use_mappings_default = bool(auto_refresh_before_pre.get("useMappingsAsDefaultItems", True))

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
        "autoRefreshBeforePre": {
            "enabled": refresh_enabled,
            "script": refresh_script,
            "listFile": refresh_list_file,
            "clean": refresh_clean,
            "useMappingsAsDefaultItems": refresh_use_mappings_default,
        },
    }


def _run_auto_refresh_fast_grep(
    cwd: Path,
    tool_name: str,
    config: dict,
    selected_mapping: dict,
    force_no_clean: bool = False,
) -> str:
    refresh = config.get("autoRefreshBeforePre", {})
    if not refresh.get("enabled", True):
        return "skip:disabled"

    script_path = Path(str(refresh.get("script", BUILD_SCRIPT)))
    if not script_path.is_absolute():
        script_path = cwd / script_path
    if not script_path.exists():
        return f"skip:script_not_found:{script_path}"

    list_file = Path(str(refresh.get("listFile", DEFAULT_LIST_FILE)))
    if not list_file.is_absolute():
        list_file = cwd / list_file

    cmd = [
        sys.executable,
        str(script_path),
        "--cwd",
        str(cwd),
        "--config",
        CONFIG_FILE,
    ]

    if force_no_clean or not refresh.get("clean", True):
        cmd.append("--no-clean")

    has_list_file = list_file.exists()
    if has_list_file:
        cmd.extend(["--list-file", str(list_file)])

    fallback_items = []
    if refresh.get("useMappingsAsDefaultItems", True):
        for mapping in config.get("mappings", []):
            virtual = str(mapping.get("virtual", "")).strip()
            if virtual:
                fallback_items.append(virtual)

    selected_virtual = str(selected_mapping.get("virtual", "")).strip()
    if selected_virtual and selected_virtual not in fallback_items:
        fallback_items.append(selected_virtual)

    if not has_list_file and fallback_items:
        cmd.extend(fallback_items)

    if not has_list_file and not fallback_items:
        return "skip:no_list_or_items"

    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except Exception as e:
        return f"error:run_failed:{e}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or f"exit:{result.returncode}"
        return f"error:exit:{details}"

    try:
        payload = json.loads(result.stdout or "{}")
        created_count = int(payload.get("created_count", 0))
        skipped_count = int(payload.get("skipped_count", 0))
        if created_count == 0 and skipped_count > 0:
            skipped = payload.get("skipped", [])
            first_reason = ""
            if isinstance(skipped, list) and skipped:
                first = skipped[0]
                if isinstance(first, dict):
                    first_reason = str(first.get("reason", "")).strip()
            if first_reason:
                return f"warn:no_created:{first_reason}"
            return "warn:no_created"
    except Exception:
        pass

    return "ok"


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


def _activate_virtual_link(cwd: Path, mapping: dict, config: dict, tool_name: str, enable_symlinks: bool = True) -> str:
    virtual_prefix = mapping["virtual"]
    real_prefix = mapping["real"]
    virtual_path = cwd / virtual_prefix
    real_path = cwd / real_prefix
    backup_path = cwd / f"{virtual_prefix}{BACKUP_SUFFIX}"

    if not enable_symlinks:
        return "skip:symlinks_disabled"

    state = _load_state(cwd)
    entries = state.setdefault("entries", {})
    entry = entries.setdefault(virtual_prefix, {"count": 0, "backup": str(backup_path)})

    if entry.get("count", 0) > 0:
        if not backup_path.exists() or not virtual_path.exists():
            entry["count"] = 0
            _save_state(cwd, state)
            _append_log(
                cwd,
                "pre-recover",
                tool_name,
                "handled",
                f"{mapping['virtual']}->{mapping['real']}:reset_stale_state",
            )
        else:
            refresh_status = _run_auto_refresh_fast_grep(cwd, tool_name, config, mapping, force_no_clean=True)
            _append_log(cwd, "pre-refresh", tool_name, "handled", f"{mapping['virtual']}->{mapping['real']}:{refresh_status}; mode=no_clean_active")
            entry["count"] = entry.get("count", 0) + 1
            _save_state(cwd, state)
            return f"active:ref+1:{entry['count']}"

    if backup_path.exists() and virtual_path.exists():
        return "skip:backup_and_virtual_both_exist"

    backup_created_now = False
    if not backup_path.exists():
        if virtual_path.exists():
            virtual_path.rename(backup_path)
            backup_created_now = True
        else:
            return "skip:virtual_missing"

    refresh_status = _run_auto_refresh_fast_grep(cwd, tool_name, config, mapping)
    _append_log(cwd, "pre-refresh", tool_name, "handled", f"{mapping['virtual']}->{mapping['real']}:{refresh_status}")

    if not real_path.exists() or not real_path.is_dir():
        if backup_created_now and backup_path.exists() and not virtual_path.exists():
            backup_path.rename(virtual_path)
        return "skip:real_missing"

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
    link_status = _activate_virtual_link(cwd, selected_mapping, config, tool_name, enable_symlinks)
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
