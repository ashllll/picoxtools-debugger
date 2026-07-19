#!/usr/bin/env python3
"""Safely preview, install, or verify this skill and MCP in Reasonix."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


PLUGIN_NAME = "picoxtools-debugger"
TRUSTED_READ_ONLY_TOOLS = [
    "safety_policy",
    "inspect_host",
    "inspect_serial_owners",
    "check_web_assets",
    "list_device_files",
    "read_device_file",
    "preview_init_config",
    "quick_start",
    "wiring_guide",
    "uart_diagnostic",
    "troubleshoot",
    "search_docs",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="back up and update the Reasonix user config")
    mode.add_argument("--check", action="store_true", help="exit non-zero unless Reasonix is already configured")
    parser.add_argument("--config", type=Path, help="explicit Reasonix config.toml path")
    parser.add_argument("--repo-root", type=Path, help="plugin repository root; defaults to the script parent")
    parser.add_argument("--python", type=Path, help="Python executable for the MCP; defaults to the interpreter running this installer")
    return parser.parse_args()


def reasonix_config_candidates(home: Path) -> list[Path]:
    candidates = [home / ".reasonix" / "config.toml"]
    system = platform.system()
    if system == "Darwin":
        candidates.append(home / "Library" / "Application Support" / "reasonix" / "config.toml")
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "reasonix" / "config.toml")
    else:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        candidates.append(config_home / "reasonix" / "config.toml")
    return list(dict.fromkeys(candidates))


def resolve_config_path(explicit: Path | None, home: Path | None = None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()
    home = home or Path.home()
    existing = [path for path in reasonix_config_candidates(home) if path.is_file()]
    if len(existing) > 1:
        rendered = ", ".join(str(path) for path in existing)
        raise ValueError(f"multiple Reasonix configs found; pass --config explicitly: {rendered}")
    if existing:
        return existing[0].resolve()
    candidates = reasonix_config_candidates(home)
    if platform.system() == "Darwin" and len(candidates) > 1:
        return candidates[1].resolve()
    return candidates[-1].resolve()


def _section_bounds(lines: list[str], header: str) -> tuple[int, int] | None:
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    return start, end


def _merge_skill_path(text: str, skill_root: Path) -> tuple[str, bool]:
    parsed = tomllib.loads(text) if text.strip() else {}
    paths = list(parsed.get("skills", {}).get("paths", []))
    desired = skill_root.resolve()
    merged: list[str] = []
    found = False
    for value in paths:
        resolved = Path(value).expanduser().resolve()
        if resolved == desired:
            if not found:
                merged.append(str(desired))
                found = True
            continue
        merged.append(value)
    if not found:
        merged.append(str(desired))
    if merged == paths:
        return text, False

    line = "paths = " + json.dumps(merged, ensure_ascii=False)
    lines = text.splitlines()
    bounds = _section_bounds(lines, "[skills]")
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[skills]", line])
    else:
        start, end = bounds
        path_index = next(
            (index for index in range(start + 1, end) if re.match(r"^\s*paths\s*=", lines[index])),
            None,
        )
        if path_index is None:
            lines.insert(start + 1, line)
        else:
            lines[path_index] = line
    return "\n".join(lines).rstrip() + "\n", True


def _plugin_block(repo_root: Path, python: Path) -> list[str]:
    server = (repo_root / "mcp" / "server.py").resolve()
    return [
        "[[plugins]]",
        f'name = "{PLUGIN_NAME}"',
        'type = "stdio"',
        f"command = {json.dumps(str(python.resolve()))}",
        "args = " + json.dumps([str(server)], ensure_ascii=False),
        "call_timeout_seconds = 30",
        "trusted_read_only_tools = " + json.dumps(TRUSTED_READ_ONLY_TOOLS),
        "",
    ]


def _replace_plugin(text: str, repo_root: Path, python: Path) -> tuple[str, bool]:
    desired_block = _plugin_block(repo_root, python)
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == "[[plugins]]"]
    matches: list[tuple[int, int]] = []
    for start in starts:
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if re.match(r"^\s*\[\[?[^]]+\]\]?\s*(?:#.*)?$", lines[index])
            ),
            len(lines),
        )
        section = "\n".join(lines[start:end])
        if re.search(rf'^\s*name\s*=\s*["\']{re.escape(PLUGIN_NAME)}["\']\s*$', section, re.M):
            matches.append((start, end))

    current = tomllib.loads(text).get("plugins", []) if text.strip() else []
    configured = [item for item in current if item.get("name") == PLUGIN_NAME]
    desired = {
        "name": PLUGIN_NAME,
        "type": "stdio",
        "command": str(python.resolve()),
        "args": [str((repo_root / "mcp" / "server.py").resolve())],
        "call_timeout_seconds": 30,
        "trusted_read_only_tools": TRUSTED_READ_ONLY_TOOLS,
    }
    if len(configured) == 1 and configured[0] == desired and len(matches) == 1:
        return text, False

    for start, end in reversed(matches):
        del lines[start:end]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines.extend(desired_block)
    return "\n".join(lines).rstrip() + "\n", True


def compose_config(text: str, repo_root: Path, python: Path) -> tuple[str, list[str]]:
    if text.strip():
        tomllib.loads(text)
    changes: list[str] = []
    text, changed = _merge_skill_path(text, repo_root / "skills")
    if changed:
        changes.append("skill path")
    text, changed = _replace_plugin(text, repo_root, python)
    if changed:
        changes.append("MCP plugin and trust policy")
    tomllib.loads(text)
    return text, changes


def atomic_write_with_backup(path: Path, text: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak.{int(time.time())}")
        shutil.copy2(path, backup)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return backup


def main() -> int:
    args = parse_args()
    repo_root = (args.repo_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    server = repo_root / "mcp" / "server.py"
    skill = repo_root / "skills" / "use-picoxtools-debugger" / "SKILL.md"
    if not server.is_file() or not skill.is_file():
        raise SystemExit(f"invalid repository root: {repo_root}")
    try:
        config_path = resolve_config_path(args.config)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    python = args.python.expanduser().resolve() if args.python else None
    if python is None and args.check and current.strip():
        parsed = tomllib.loads(current)
        configured = [item for item in parsed.get("plugins", []) if item.get("name") == PLUGIN_NAME]
        if len(configured) == 1 and isinstance(configured[0].get("command"), str):
            python = Path(configured[0]["command"])
    python = python or Path(sys.executable)
    if not python.is_file():
        print(f"Error: Python executable does not exist: {python}", file=sys.stderr)
        return 2
    updated, changes = compose_config(current, repo_root, python)

    print(f"Reasonix config: {config_path}")
    print(f"Skill root: {repo_root / 'skills'}")
    print(f"MCP server: {server}")
    print(f"MCP Python: {python.resolve()}")
    if not changes:
        print("Status: already configured")
        return 0
    print("Pending changes: " + ", ".join(changes))
    if args.check:
        print("Status: configuration update required")
        return 1
    if not args.apply:
        print("Dry run only; rerun with --apply to back up and update the config.")
        return 0
    backup = atomic_write_with_backup(config_path, updated)
    if backup:
        print(f"Backup: {backup}")
    print("Status: configured; restart Reasonix, then inspect /skills and /mcp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
