import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List

BACKUP_SUFFIX = ".__fastgrep_backup"
DEFAULT_CONFIG_FILE = ".github/hooks/fast_grep_config.json"


def _normalize_slashes(value: str) -> str:
    return value.replace("\\", "/")


def _load_config(cwd: Path, config_path: Path) -> dict:
    default_config = {"mappings": []}
    full_path = config_path if config_path.is_absolute() else (cwd / config_path)
    if not full_path.exists():
        return default_config
    try:
        raw = json.loads(full_path.read_text(encoding="utf-8"))
    except Exception:
        return default_config

    mappings = raw.get("mappings", [])
    normalized = []
    if isinstance(mappings, list):
        for item in mappings:
            if not isinstance(item, dict):
                continue
            virtual = str(item.get("virtual", "")).strip()
            enabled = bool(item.get("enabled", True))
            if virtual and enabled:
                normalized.append({"virtual": virtual})

    return {"mappings": normalized}


def _read_list_file(list_file: Path) -> List[str]:
    text = list_file.read_text(encoding="utf-8")
    stripped = text.strip()

    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass

    items: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _remove_existing(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _create_link_windows(link_path: Path, target_path: Path, is_dir: bool) -> None:
    _ensure_parent(link_path)

    if is_dir:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return

    try:
        os.symlink(str(target_path), str(link_path), target_is_directory=False)
    except OSError:
        subprocess.run(
            ["cmd", "/c", "mklink", "/H", str(link_path), str(target_path)],
            check=True,
            capture_output=True,
            text=True,
        )


def _create_link_unix(link_path: Path, target_path: Path, is_dir: bool) -> None:
    _ensure_parent(link_path)
    os.symlink(str(target_path), str(link_path), target_is_directory=is_dir)


def _create_link(link_path: Path, target_path: Path, is_dir: bool) -> None:
    _remove_existing(link_path)
    if os.name == "nt":
        _create_link_windows(link_path, target_path, is_dir)
    else:
        _create_link_unix(link_path, target_path, is_dir)


def _to_abs_under_cwd(raw: str, cwd: Path) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return (cwd / candidate).resolve(strict=False)


def _to_rel_under_cwd(path: Path, cwd: Path) -> Path:
    try:
        return path.relative_to(cwd)
    except ValueError:
        raise ValueError(f"Path is outside cwd and cannot be mirrored into .fast-grep: {path}")


def _apply_backup_suffix(abs_path: Path, cwd: Path, mappings: Iterable[dict]) -> Path:
    normalized_path = _normalize_slashes(str(abs_path))
    normalized_cwd = _normalize_slashes(str(cwd)).rstrip("/")

    changed = True
    while changed:
        changed = False
        for mapping in mappings:
            virtual = _normalize_slashes(str(mapping["virtual"]).strip("/"))
            if not virtual:
                continue

            source_root = f"{normalized_cwd}/{virtual}"
            backup_root = f"{normalized_cwd}/{virtual}{BACKUP_SUFFIX}"

            if normalized_path == source_root:
                normalized_path = backup_root
                changed = True
            elif normalized_path.startswith(source_root + "/"):
                normalized_path = backup_root + normalized_path[len(source_root):]
                changed = True

    if os.name == "nt":
        return Path(normalized_path.replace("/", "\\"))
    return Path(normalized_path)


def _clean_fast_grep_dir(fast_grep_dir: Path) -> None:
    if fast_grep_dir.exists() or fast_grep_dir.is_symlink():
        if fast_grep_dir.is_symlink() or fast_grep_dir.is_file():
            fast_grep_dir.unlink()
        else:
            shutil.rmtree(fast_grep_dir)
    fast_grep_dir.mkdir(parents=True, exist_ok=True)


def build_fast_grep_links(cwd: Path, fast_grep_dir: Path, mappings: List[dict], raw_items: List[str], clean: bool) -> dict:
    if clean:
        _clean_fast_grep_dir(fast_grep_dir)
    else:
        fast_grep_dir.mkdir(parents=True, exist_ok=True)

    created = []
    skipped = []

    for raw in raw_items:
        raw = raw.strip()
        if not raw:
            continue

        source_abs = _to_abs_under_cwd(raw, cwd)
        source_with_backup = _apply_backup_suffix(source_abs, cwd, mappings)

        if not source_with_backup.exists():
            skipped.append({"item": raw, "reason": f"source_not_found:{source_with_backup}"})
            continue

        try:
            rel = _to_rel_under_cwd(source_abs, cwd)
        except ValueError as e:
            skipped.append({"item": raw, "reason": str(e)})
            continue

        if rel.parts and rel.parts[0] == ".fast-grep":
            skipped.append({"item": raw, "reason": "item_inside_fast_grep"})
            continue

        link_path = fast_grep_dir / rel
        is_dir = source_with_backup.is_dir()

        try:
            _create_link(link_path, source_with_backup, is_dir)
            created.append(
                {
                    "item": raw,
                    "link": str(link_path),
                    "target": str(source_with_backup),
                    "type": "dir" if is_dir else "file",
                }
            )
        except Exception as e:
            skipped.append({"item": raw, "reason": f"link_create_failed:{e}"})

    return {"created": created, "skipped": skipped}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build .fast-grep shortcuts from a file/folder list. "
            "For paths under virtual mappings, target path is rewritten to include __fastgrep_backup."
        )
    )
    parser.add_argument("--cwd", default=".", help="Workspace root (default: current directory)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="Path to fast_grep_config.json")
    parser.add_argument("--fast-grep-dir", default=".fast-grep", help="Output .fast-grep directory")
    parser.add_argument("--list-file", help="Path list file. Supports JSON array or newline-separated text")
    parser.add_argument("--no-clean", action="store_true", help="Do not clean .fast-grep before building")
    parser.add_argument("items", nargs="*", help="Optional direct file/folder items")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    cwd = Path(args.cwd).resolve(strict=False)
    config = _load_config(cwd, Path(args.config))
    mappings = config.get("mappings", [])

    raw_items: List[str] = []
    if args.list_file:
        list_file = Path(args.list_file)
        if not list_file.is_absolute():
            list_file = cwd / list_file
        if not list_file.exists():
            print(json.dumps({"error": f"list_file_not_found:{list_file}"}, ensure_ascii=False))
            return 1
        raw_items.extend(_read_list_file(list_file))

    raw_items.extend(args.items)

    if not raw_items:
        print(json.dumps({"error": "no_items"}, ensure_ascii=False))
        return 1

    fast_grep_dir = Path(args.fast_grep_dir)
    if not fast_grep_dir.is_absolute():
        fast_grep_dir = cwd / fast_grep_dir

    result = build_fast_grep_links(
        cwd=cwd,
        fast_grep_dir=fast_grep_dir,
        mappings=mappings,
        raw_items=raw_items,
        clean=not args.no_clean,
    )

    output = {
        "cwd": str(cwd),
        "fast_grep_dir": str(fast_grep_dir),
        "mappings": mappings,
        "created_count": len(result["created"]),
        "skipped_count": len(result["skipped"]),
        "created": result["created"],
        "skipped": result["skipped"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
