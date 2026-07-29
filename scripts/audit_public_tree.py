#!/usr/bin/env python3
"""Fail closed when the public repository contains non-public material."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ROOT_FILES = {
    "README.md",
    "README.en.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "TRADEMARKS.md",
    "SECURITY.md",
    "PRIVACY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "CITATION.cff",
}
EXPECTED_SKILLS = {
    "fact-check-x-complete": "fact-check-x-complete",
    "fact-check-x-unified": "fact-check-x-unified",
    "fact-check-x-authoritative-verify": "fact-check-x-authoritative-verify",
    "fact-check-x-knowledge-compare": "fact-check-x-knowledge-compare",
    "llm-answer-reference-compare": "llm-answer-reference-compare",
}
FORBIDDEN_DIR_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "browser-profiles",
    "profiles",
    "sessions",
    "runs",
    "backups",
}
FORBIDDEN_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "release-receipt.json",
    "Cookies",
    "Login Data",
    "Web Data",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".zip", ".skill", ".bak", ".backup"}
TEXT_SUFFIXES = {
    "",
    ".cff",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}
CONTENT_RULES = {
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "github_token": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    ),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE
    ),
    "assigned_trusted_search_key": re.compile(
        r"\bTRUSTED_SEARCH_KEY\s*=\s*['\"]?[A-Za-z0-9._~+/-]{16,}"
    ),
    "mac_user_path": re.compile("/" + r"Users/[^/\s'\"<>]+/"),
    "linux_user_path": re.compile("/" + r"home/[^/\s'\"<>]+/"),
    "windows_user_path": re.compile(
        r"[A-Za-z]:\\Users\\[^\\\s'\"<>]+\\", re.IGNORECASE
    ),
    "file_user_url": re.compile(
        r"file:///(?:Users|home)/[^/\s'\"<>]+/", re.IGNORECASE
    ),
    "private_ipv4": re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    ),
}


def frontmatter_name(skill_file: Path) -> str | None:
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    for line in text[4:end].splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def scan(root: Path) -> dict:
    findings: list[dict[str, str]] = []
    files_scanned = 0
    bytes_scanned = 0

    missing_root = sorted(name for name in REQUIRED_ROOT_FILES if not (root / name).is_file())
    for name in missing_root:
        findings.append({"rule": "missing_root_file", "path": name})

    skills_root = root / "skills"
    for directory, expected_name in EXPECTED_SKILLS.items():
        skill_file = skills_root / directory / "SKILL.md"
        if not skill_file.is_file():
            findings.append(
                {"rule": "missing_skill", "path": str(skill_file.relative_to(root))}
            )
            continue
        actual_name = frontmatter_name(skill_file)
        if actual_name != expected_name:
            findings.append(
                {
                    "rule": "skill_name_mismatch",
                    "path": str(skill_file.relative_to(root)),
                    "detail": f"expected {expected_name}, got {actual_name}",
                }
            )

    component_license = (
        skills_root
        / "llm-answer-reference-compare"
        / "assets"
        / "tool"
        / "LICENSE"
    )
    if not component_license.is_file():
        findings.append(
            {
                "rule": "missing_third_party_license",
                "path": str(component_license.relative_to(root)),
            }
        )

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            findings.append({"rule": "symlink", "path": relative.as_posix()})
            continue
        if path.is_dir():
            if path.name in FORBIDDEN_DIR_NAMES:
                findings.append(
                    {"rule": "forbidden_directory", "path": relative.as_posix()}
                )
            continue
        if not path.is_file():
            findings.append({"rule": "special_file", "path": relative.as_posix()})
            continue

        files_scanned += 1
        bytes_scanned += path.stat().st_size
        if path.name in FORBIDDEN_FILE_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            findings.append({"rule": "forbidden_file", "path": relative.as_posix()})
            continue

        mode = path.stat().st_mode
        if stat.S_ISREG(mode) and path.suffix not in TEXT_SUFFIXES:
            findings.append(
                {"rule": "unexpected_binary_or_type", "path": relative.as_posix()}
            )
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append({"rule": "non_utf8_file", "path": relative.as_posix()})
            continue

        for rule, pattern in CONTENT_RULES.items():
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    {
                        "rule": rule,
                        "path": relative.as_posix(),
                        "detail": f"line {line}",
                    }
                )

    return {
        "schemaVersion": "fact-check-x/public-tree-audit@1",
        "status": "passed" if not findings else "failed",
        "root": root.name,
        "filesScanned": files_scanned,
        "bytesScanned": bytes_scanned,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    result = scan(Path(args.root).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
