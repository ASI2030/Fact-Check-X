#!/usr/bin/env python3
"""Build and validate a deterministic SkillHub package from the complete ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath


ZIP_TIME = (2026, 7, 30, 0, 0, 0)
ROOT = "fact-check-x-complete"
REQUIRED_PLATFORMS = (
    "深知晓",
    "深知晓（深度研究）",
    "豆包",
    "腾讯元宝",
    "DeepSeek",
    "通义千问",
)
FORBIDDEN_MARKET_TERMS = (
    "V8",
    "1.0 原始",
    "1.1 知识",
    "云端权威核验",
    "定稿报告",
    "[WorkBuddy 验收标准]",
)
RELATIVE_LINK = re.compile(r"\]\((?!https?://|mailto:|#|<返回路径>)[^)]+\)")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.name == "LICENSE":
        return path.with_name("LICENSE.txt").as_posix()
    if path.name == "NOTICE":
        return path.with_name("NOTICE.txt").as_posix()
    return path.as_posix()


def validate_skill(skill_text: str, version: str) -> None:
    if f'version: "{version}"' not in skill_text:
        raise RuntimeError(f"SKILL.md version is not {version}")
    missing = [name for name in REQUIRED_PLATFORMS if name not in skill_text]
    if missing:
        raise RuntimeError(f"SKILL.md misses supported platforms: {missing}")
    forbidden = [term for term in FORBIDDEN_MARKET_TERMS if term in skill_text]
    if forbidden:
        raise RuntimeError(f"SKILL.md contains internal product terms: {forbidden}")
    links = RELATIVE_LINK.findall(skill_text)
    if links:
        raise RuntimeError(f"SKILL.md contains SkillHub-breaking relative links: {links}")


def build(source: Path, output: Path, version: str) -> dict:
    with zipfile.ZipFile(source) as archive:
        if archive.testzip():
            raise RuntimeError("source ZIP is corrupt")
        names = set(archive.namelist())
        skill_name = f"{ROOT}/SKILL.md"
        if skill_name not in names:
            raise RuntimeError(f"source ZIP misses {skill_name}")
        validate_skill(archive.read(skill_name).decode("utf-8"), version)

        files: list[tuple[str, bytes, int]] = []
        seen: set[str] = set()
        for info in archive.infolist():
            if info.is_dir():
                continue
            original = PurePosixPath(info.filename)
            if (
                original.is_absolute()
                or ".." in original.parts
                or not original.parts
                or original.parts[0] != ROOT
            ):
                raise RuntimeError(f"unsafe source path: {info.filename}")
            target = normalized_name(info.filename)
            if target in seen:
                raise RuntimeError(f"duplicate normalized path: {target}")
            seen.add(target)
            mode = (info.external_attr >> 16) & 0o777
            files.append((target, archive.read(info.filename), mode or 0o644))

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        for name, content, mode in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, content)

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        if f"{ROOT}/SKILL.md" not in names:
            raise RuntimeError("output ZIP misses SKILL.md")
        if any(PurePosixPath(name).name in {"LICENSE", "NOTICE"} for name in names):
            raise RuntimeError("output ZIP contains extensionless legal files")
        validate_skill(
            archive.read(f"{ROOT}/SKILL.md").decode("utf-8"),
            version,
        )

    return {
        "status": "built",
        "version": version,
        "source": str(source),
        "output": str(output),
        "bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "files": len(files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    result = build(
        Path(args.source).resolve(),
        Path(args.output).resolve(),
        args.version,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
