#!/usr/bin/env python3
"""Verify Fact-Check-X files and release manifests without installing them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "browser-profiles",
    "profiles",
    "sessions",
    "runs",
}
FORBIDDEN_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "release-receipt.json",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        raise ValueError("expected SHA must be 64 lowercase hexadecimal characters")
    return normalized


def inspect_zip(
    path: Path,
    expected_root: str | None = None,
    require_public_legal: bool = True,
) -> dict:
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"corrupt ZIP member: {bad_member}")

        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("duplicate ZIP members are not allowed")
        roots = set()
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if not member.parts:
                continue
            roots.add(member.parts[0])
            if member.is_absolute() or ".." in member.parts:
                raise RuntimeError(f"unsafe ZIP path: {info.filename}")
            if any(part in FORBIDDEN_PARTS for part in member.parts):
                raise RuntimeError(f"forbidden ZIP path: {info.filename}")
            if member.name in FORBIDDEN_NAMES or member.suffix in {".pyc", ".pyo"}:
                raise RuntimeError(f"forbidden ZIP file: {info.filename}")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise RuntimeError(f"symbolic link is not allowed: {info.filename}")

        if len(roots) != 1:
            raise RuntimeError(f"expected one ZIP root, found: {sorted(roots)}")
        root = next(iter(roots))
        if expected_root and root != expected_root:
            raise RuntimeError(
                f"expected ZIP root {expected_root!r}, found {root!r}"
            )

        required = set()
        if require_public_legal:
            required.update({f"{root}/LICENSE", f"{root}/NOTICE"})
        if root == "fact-check-x-suite":
            required.add(f"{root}/README.md")
        else:
            required.add(f"{root}/SKILL.md")
        missing = sorted(required - set(names))
        if missing:
            raise RuntimeError(f"missing required ZIP members: {missing}")

        return {
            "root": root,
            "files": len(names),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }


def verify_one(path: Path, expected_sha: str, inspect: bool) -> dict:
    expected = normalize_sha(expected_sha)
    actual = sha256_file(path)
    if actual != expected:
        raise RuntimeError(
            f"SHA256 mismatch for {path.name}: expected {expected}, actual {actual}"
        )
    result = {
        "status": "verified",
        "file": str(path),
        "sha256": actual,
        "bytes": path.stat().st_size,
    }
    if inspect:
        result["archive"] = inspect_zip(path)
    return result


def verify_manifest(manifest_path: Path, asset_dir: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "fact-check-x/public-release-manifest@1":
        raise RuntimeError("unsupported release manifest schema")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("manifest has no artifacts")

    verified = []
    seen = set()
    for item in artifacts:
        name = item.get("file")
        expected_sha = item.get("sha256")
        expected_bytes = item.get("bytes")
        expected_root = item.get("root")
        if not isinstance(name, str) or name in seen or Path(name).name != name:
            raise RuntimeError(f"invalid or duplicate artifact name: {name!r}")
        seen.add(name)
        path = asset_dir / name
        if not path.is_file():
            raise RuntimeError(f"missing artifact: {name}")
        actual_sha = sha256_file(path)
        if actual_sha != normalize_sha(expected_sha):
            raise RuntimeError(f"SHA256 mismatch: {name}")
        if path.stat().st_size != expected_bytes:
            raise RuntimeError(f"byte-size mismatch: {name}")
        details = inspect_zip(path, expected_root)
        if details["files"] != item.get("files"):
            raise RuntimeError(f"file-count mismatch: {name}")
        verified.append(details)

    formal_item = manifest.get("formalArtifact")
    verified_formal = None
    if formal_item is not None:
        name = formal_item.get("file")
        expected_sha = formal_item.get("sha256")
        expected_bytes = formal_item.get("bytes")
        expected_root = formal_item.get("root")
        if not isinstance(name, str) or Path(name).name != name:
            raise RuntimeError("invalid formal artifact filename")
        path = asset_dir / name
        if not path.is_file():
            raise RuntimeError(f"missing formal artifact: {name}")
        actual_sha = sha256_file(path)
        if actual_sha != normalize_sha(expected_sha):
            raise RuntimeError(f"SHA256 mismatch: {name}")
        if path.stat().st_size != expected_bytes:
            raise RuntimeError(f"byte-size mismatch: {name}")
        verified_formal = inspect_zip(
            path,
            expected_root,
            require_public_legal=False,
        )

    supply_chain = manifest.get("supplyChain") or {}
    verified_supply_chain = {}
    for field in ("sha256Sums", "sbomSpdx", "sbomCycloneDx"):
        item = supply_chain.get(field)
        if not isinstance(item, dict):
            raise RuntimeError(f"missing supply-chain item: {field}")
        name = item.get("file")
        expected_sha = item.get("sha256")
        if not isinstance(name, str) or Path(name).name != name:
            raise RuntimeError(f"invalid supply-chain filename: {field}")
        path = asset_dir / name
        if not path.is_file():
            raise RuntimeError(f"missing supply-chain file: {name}")
        actual_sha = sha256_file(path)
        if actual_sha != normalize_sha(expected_sha):
            raise RuntimeError(f"SHA256 mismatch: {name}")
        verified_supply_chain[field] = {
            "file": name,
            "sha256": actual_sha,
        }

    checksum_file = asset_dir / verified_supply_chain["sha256Sums"]["file"]
    declared_checksums = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, name = line.split("  ", 1)
        except ValueError as error:
            raise RuntimeError("invalid SHA256SUMS format") from error
        declared_checksums[name] = normalize_sha(digest)
    expected_checksum_names = {
        item["file"] for item in artifacts
    } | {
        verified_supply_chain["sbomSpdx"]["file"],
        verified_supply_chain["sbomCycloneDx"]["file"],
    }
    if formal_item is not None:
        expected_checksum_names.add(formal_item["file"])
    if set(declared_checksums) != expected_checksum_names:
        raise RuntimeError("SHA256SUMS file set does not match the manifest")
    for name, expected_sha in declared_checksums.items():
        if sha256_file(asset_dir / name) != expected_sha:
            raise RuntimeError(f"SHA256SUMS mismatch: {name}")

    return {
        "status": "verified",
        "manifest": str(manifest_path),
        "releaseStatus": manifest.get("status"),
        "version": manifest.get("version"),
        "formalArtifact": verified_formal,
        "artifacts": verified,
        "supplyChain": verified_supply_chain,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    sha_parser = subparsers.add_parser("sha", help="Verify one file SHA256.")
    sha_parser.add_argument("--file", required=True)
    sha_parser.add_argument("--sha256", required=True)
    sha_parser.add_argument(
        "--inspect-zip",
        action="store_true",
        help="Also inspect ZIP safety and required files.",
    )

    manifest_parser = subparsers.add_parser(
        "manifest", help="Verify all assets named by a release manifest."
    )
    manifest_parser.add_argument("--manifest", required=True)
    manifest_parser.add_argument("--asset-dir", required=True)

    args = parser.parse_args()
    if args.command == "sha":
        result = verify_one(
            Path(args.file).resolve(),
            args.sha256,
            args.inspect_zip,
        )
    else:
        result = verify_manifest(
            Path(args.manifest).resolve(),
            Path(args.asset_dir).resolve(),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
