#!/usr/bin/env python3
"""Build deterministic public Fact-Check-X release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from generate_sbom import generate as generate_sbom


ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = ROOT / "skills"
MODULES = (
    "llm-answer-reference-compare",
    "fact-check-x-knowledge-compare",
    "fact-check-x-authoritative-verify",
    "fact-check-x-unified",
)
COMPLETE = "fact-check-x-complete"
LEGAL_FILES = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "TRADEMARKS.md",
    "PRIVACY.md",
    "SECURITY.md",
)
EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "browser-profiles",
    "profiles",
    "sessions",
    "runs",
}
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}
ZIP_TIME = (2026, 7, 29, 0, 0, 0)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def public_files(directory: Path):
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix in {".pyc", ".pyo"}:
            continue
        yield path


def tree_sha256(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in public_files(directory):
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def file_mode(path: Path) -> int:
    if path.suffix in {".py", ".sh"} or path.name == "cli.js":
        return 0o755
    return 0o644


def add_bytes(
    archive: zipfile.ZipFile,
    content: bytes,
    arcname: PurePosixPath | str,
    mode: int = 0o644,
) -> None:
    name = PurePosixPath(arcname).as_posix()
    info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    archive.writestr(info, content)


def add_file(
    archive: zipfile.ZipFile,
    source: Path,
    arcname: PurePosixPath | str,
) -> None:
    add_bytes(archive, source.read_bytes(), arcname, file_mode(source))


def add_legal(archive: zipfile.ZipFile, root: PurePosixPath) -> None:
    for name in LEGAL_FILES:
        add_file(archive, ROOT / name, root / name)


def versioned_name(name: str, version: str) -> str:
    return f"{name}-v{version}.zip"


def write_individual(out_dir: Path, skill_name: str, version: str) -> Path:
    target = out_dir / versioned_name(skill_name, version)
    source_root = SKILLS_ROOT / skill_name
    archive_root = PurePosixPath(skill_name)
    with zipfile.ZipFile(target, "w") as archive:
        for path in public_files(source_root):
            add_file(
                archive,
                path,
                archive_root / path.relative_to(source_root).as_posix(),
            )
        add_legal(archive, archive_root)
    return target


def write_suite(out_dir: Path, version: str) -> Path:
    target = out_dir / versioned_name("fact-check-x-suite", version)
    archive_root = PurePosixPath("fact-check-x-suite")
    with zipfile.ZipFile(target, "w") as archive:
        add_file(archive, ROOT / "README.md", archive_root / "README.md")
        add_file(archive, ROOT / "README.en.md", archive_root / "README.en.md")
        add_legal(archive, archive_root)
        for skill_name in MODULES:
            source_root = SKILLS_ROOT / skill_name
            for path in public_files(source_root):
                add_file(
                    archive,
                    path,
                    archive_root
                    / skill_name
                    / path.relative_to(source_root).as_posix(),
                )
    return target


def write_complete(
    out_dir: Path,
    version: str,
    candidate_sha: str | None,
    official_sha: str | None,
) -> Path:
    target = out_dir / versioned_name(COMPLETE, version)
    source_root = SKILLS_ROOT / COMPLETE
    archive_root = PurePosixPath(COMPLETE)
    unified_root = SKILLS_ROOT / "fact-check-x-unified"

    with zipfile.ZipFile(target, "w") as archive:
        written: dict[str, bytes] = {}

        def add_unique(source: Path, arcname: PurePosixPath | str) -> None:
            name = PurePosixPath(arcname).as_posix()
            content = source.read_bytes()
            if name in written:
                if written[name] != content:
                    raise RuntimeError(
                        f"conflicting complete-package source for {name}"
                    )
                return
            add_bytes(archive, content, name, file_mode(source))
            written[name] = content

        for path in public_files(source_root):
            relative = path.relative_to(source_root)
            if relative == Path("package-manifest.json") or (
                len(relative.parts) == 1 and relative.name in LEGAL_FILES
            ):
                continue
            add_unique(
                path,
                archive_root / relative.as_posix(),
            )

        for path in public_files(unified_root / "scripts"):
            add_unique(
                path,
                archive_root
                / "scripts"
                / path.relative_to(unified_root / "scripts").as_posix(),
            )

        for skill_name in MODULES[:3]:
            module_root = SKILLS_ROOT / skill_name
            for path in public_files(module_root):
                relative = path.relative_to(module_root)
                if relative == Path("SKILL.md") or relative.parts[0] == "agents":
                    continue
                add_unique(
                    path,
                    archive_root / "modules" / skill_name / relative.as_posix(),
                )

        for path in public_files(unified_root / "tests"):
            relative = path.relative_to(unified_root / "tests")
            if relative.name == "smoke_test.py":
                relative = relative.with_name("unified_smoke_test.py")
            add_unique(path, archive_root / "tests" / relative.as_posix())

        fixture_sources = {
            "results.json": SKILLS_ROOT
            / "fact-check-x-knowledge-compare"
            / "tests"
            / "fixtures"
            / "results.json",
            "comparison-analysis.json": SKILLS_ROOT
            / "fact-check-x-knowledge-compare"
            / "tests"
            / "fixtures"
            / "comparison-analysis.json",
            "K1-assessment.json": SKILLS_ROOT
            / "fact-check-x-authoritative-verify"
            / "tests"
            / "fixtures"
            / "K1-assessment.json",
        }
        for name, source in fixture_sources.items():
            add_unique(source, archive_root / "tests" / "fixtures" / name)

        package_manifest = {
            "schemaVersion": "fact-check-x/public-workbuddy-package@1",
            "version": version,
            "entrySkill": COMPLETE,
            "bundledModules": list(MODULES[:3]),
            "externalModelApi": False,
            "skillSourceTreeSha256": tree_sha256(SKILLS_ROOT),
            "upstreamCandidateSha256": candidate_sha,
            "officialPromoteSha256": official_sha,
        }
        manifest_name = (archive_root / "package-manifest.json").as_posix()
        manifest_content = (
            json.dumps(package_manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        add_bytes(archive, manifest_content, manifest_name)
        written[manifest_name] = manifest_content
        for name in LEGAL_FILES:
            add_unique(ROOT / name, archive_root / name)
    return target


def validate_archive(
    path: Path,
    expected_root: str,
    require_public_legal: bool = True,
) -> dict[str, int | str]:
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"{path.name} is corrupt at {bad_member}")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError(f"{path.name} contains duplicate ZIP members")
        roots = {
            PurePosixPath(name).parts[0]
            for name in names
            if PurePosixPath(name).parts
        }
        if roots != {expected_root}:
            raise RuntimeError(f"{path.name} has unexpected roots: {sorted(roots)}")
        for name in names:
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts:
                raise RuntimeError(f"{path.name} has unsafe path: {name}")
            if any(part in EXCLUDED_PARTS for part in member.parts):
                raise RuntimeError(f"{path.name} has excluded path: {name}")
            if member.name in EXCLUDED_NAMES or member.suffix in {".pyc", ".pyo"}:
                raise RuntimeError(f"{path.name} has excluded file: {name}")
        required = {f"{expected_root}/SKILL.md"}
        if require_public_legal:
            required.update(
                {
                    f"{expected_root}/LICENSE",
                    f"{expected_root}/NOTICE",
                }
            )
        if expected_root == "fact-check-x-suite":
            required.remove(f"{expected_root}/SKILL.md")
            required.add(f"{expected_root}/README.md")
        missing = sorted(required - set(names))
        if missing:
            raise RuntimeError(f"{path.name} misses required files: {missing}")
        return {
            "file": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "files": len(names),
            "uncompressedBytes": sum(item.file_size for item in archive.infolist()),
            "root": expected_root,
        }


def valid_sha(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic public Fact-Check-X archives."
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--upstream-candidate-sha")
    parser.add_argument("--official-complete-sha")
    parser.add_argument("--formal-complete-file")
    args = parser.parse_args()

    candidate_sha = valid_sha(args.upstream_candidate_sha, "candidate SHA")
    official_sha = valid_sha(args.official_complete_sha, "official SHA")
    if official_sha and candidate_sha and official_sha != candidate_sha:
        raise SystemExit("official SHA does not match the frozen candidate SHA")

    out_dir = Path(args.out_dir).resolve()
    skills_resolved = SKILLS_ROOT.resolve()
    if out_dir == skills_resolved or skills_resolved in out_dir.parents:
        raise SystemExit("release output must not be written inside skills/")
    out_dir.mkdir(parents=True, exist_ok=True)

    formal_artifact = None
    formal_target = None
    if args.formal_complete_file:
        if not official_sha:
            raise SystemExit(
                "--formal-complete-file requires --official-complete-sha"
            )
        formal_source = Path(args.formal_complete_file).resolve()
        if (
            not formal_source.is_file()
            or formal_source.name != "fact-check-x-complete.zip"
        ):
            raise SystemExit(
                "formal complete file must be fact-check-x-complete.zip"
            )
        if sha256_file(formal_source) != official_sha:
            raise SystemExit("formal complete file SHA does not match official SHA")
        formal_target = out_dir / formal_source.name
        if formal_target.resolve() != formal_source:
            formal_target.write_bytes(formal_source.read_bytes())
        formal_artifact = validate_archive(
            formal_target,
            COMPLETE,
            require_public_legal=False,
        )

    outputs = [
        write_individual(out_dir, name, args.version) for name in MODULES
    ]
    outputs.append(write_suite(out_dir, args.version))
    outputs.append(
        write_complete(
            out_dir,
            args.version,
            candidate_sha,
            official_sha,
        )
    )

    artifacts = []
    for path in outputs:
        root = path.name.removesuffix(f"-v{args.version}.zip")
        artifacts.append(validate_archive(path, root))

    spdx_path, cdx_path = generate_sbom(out_dir, args.version)
    checksum_inputs = outputs + [spdx_path, cdx_path]
    if formal_target:
        checksum_inputs.append(formal_target)
    checksum_inputs = sorted(checksum_inputs, key=lambda item: item.name)
    checksums_path = out_dir / "SHA256SUMS"
    checksums_path.write_text(
        "".join(
            f"{sha256_file(path)}  {path.name}\n" for path in checksum_inputs
        ),
        encoding="utf-8",
    )

    manifest = {
        "schemaVersion": "fact-check-x/public-release-manifest@1",
        "status": "promote_verified" if official_sha else "awaiting_promote",
        "version": args.version,
        "releaseTime": None,
        "repository": "https://github.com/ASI2030/Fact-Check-X",
        "productNameZh": "全知晓",
        "productNameEn": "Fact-Check-X",
        "skillSourceTreeSha256": tree_sha256(SKILLS_ROOT),
        "upstream": {
            "candidateCompleteSha256": candidate_sha,
            "officialPromoteCompleteSha256": official_sha,
        },
        "formalArtifact": formal_artifact,
        "artifacts": artifacts,
        "supplyChain": {
            "license": "Apache-2.0",
            "thirdPartyNotices": "THIRD_PARTY_NOTICES.md",
            "sha256Sums": {
                "file": checksums_path.name,
                "sha256": sha256_file(checksums_path),
            },
            "signature": None,
            "sbomSpdx": {
                "file": spdx_path.name,
                "sha256": sha256_file(spdx_path),
            },
            "sbomCycloneDx": {
                "file": cdx_path.name,
                "sha256": sha256_file(cdx_path),
            },
            "provenance": None,
        },
    }
    manifest_path = out_dir / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "outDir": str(out_dir),
                "manifest": str(manifest_path),
                "artifacts": artifacts,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
