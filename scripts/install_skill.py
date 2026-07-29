#!/usr/bin/env python3
"""Install one Fact-Check-X skill archive with collision and path safety checks."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "browser-profiles",
    "profiles",
    "sessions",
    "runs",
}


def validate_zip(path: Path) -> tuple[str, list[zipfile.ZipInfo]]:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("archive integrity check failed")
        infos = archive.infolist()
        roots = set()
        for info in infos:
            member = PurePosixPath(info.filename)
            if not member.parts:
                continue
            roots.add(member.parts[0])
            if member.is_absolute() or ".." in member.parts:
                raise RuntimeError(f"unsafe archive path: {info.filename}")
            if any(part in FORBIDDEN_PARTS for part in member.parts):
                raise RuntimeError(f"forbidden archive path: {info.filename}")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise RuntimeError(f"symbolic link is not allowed: {info.filename}")
        if len(roots) != 1:
            raise RuntimeError(f"expected one skill root, found {sorted(roots)}")
        root = next(iter(roots))
        if root == "fact-check-x-suite":
            raise RuntimeError(
                "the suite archive contains multiple skills; use a single-skill archive"
            )
        if f"{root}/SKILL.md" not in {item.filename for item in infos}:
            raise RuntimeError("archive root does not contain SKILL.md")
        return root, infos


def destination_base(target: str, project_dir: Path | None) -> Path | None:
    if target == "workbuddy":
        return None
    if target == "codebuddy":
        return (
            project_dir / ".codebuddy" / "skills"
            if project_dir
            else Path.home() / ".codebuddy" / "skills"
        )
    if target == "codex":
        if project_dir:
            raise ValueError(
                "Codex project installation is not automated; use a user install"
            )
        codex_root = Path(
            os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        )
        return codex_root / "skills"
    if target == "claude-code":
        return (
            project_dir / ".claude" / "skills"
            if project_dir
            else Path.home() / ".claude" / "skills"
        )
    raise ValueError(f"unsupported target: {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Single-skill ZIP archive.")
    parser.add_argument(
        "--target",
        required=True,
        choices=("workbuddy", "codebuddy", "codex", "claude-code"),
    )
    parser.add_argument(
        "--project-dir",
        help="Install at project scope where the target supports it.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Move an existing skill to a timestamped backup before installation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show the destination without writing it.",
    )
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise SystemExit("--source must be an existing ZIP file")
    skill_name, _ = validate_zip(source)
    project_dir = Path(args.project_dir).resolve() if args.project_dir else None
    base = destination_base(args.target, project_dir)

    if args.target == "workbuddy":
        print(
            json.dumps(
                {
                    "status": "manual_upload_required",
                    "target": "workbuddy",
                    "skill": skill_name,
                    "source": str(source),
                    "instruction": (
                        "Open WorkBuddy Skills, choose Add skill > Upload skill, "
                        "and select this validated ZIP."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    assert base is not None
    destination = base / skill_name
    result = {
        "status": "dry_run" if args.dry_run else "installed",
        "target": args.target,
        "scope": "project" if project_dir else "user",
        "skill": skill_name,
        "source": str(source),
        "destination": str(destination),
        "backup": None,
    }
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    base.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not args.replace:
            raise SystemExit(
                f"destination already exists: {destination}; use --replace to back it up"
            )
        backup = destination.with_name(
            f"{destination.name}.backup-{time.strftime('%Y%m%dT%H%M%S')}"
        )
        if backup.exists():
            raise SystemExit(f"backup destination already exists: {backup}")
        destination.rename(backup)
        result["backup"] = str(backup)

    try:
        with tempfile.TemporaryDirectory(prefix="fact-check-x-install-") as temp:
            temp_root = Path(temp)
            with zipfile.ZipFile(source) as archive:
                archive.extractall(temp_root)
            extracted = temp_root / skill_name
            if not (extracted / "SKILL.md").is_file():
                raise RuntimeError("extracted skill is missing SKILL.md")
            shutil.copytree(extracted, destination)
    except Exception:
        backup_value = result.get("backup")
        if backup_value and not destination.exists():
            Path(backup_value).rename(destination)
        raise

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
