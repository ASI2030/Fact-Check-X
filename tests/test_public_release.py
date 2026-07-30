from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
VERSION = "1.1.0"
OFFICIAL_SHA = "7eb4b713be58d3948ca6c44db8dbe9e352a718531eb58dcdf017bff6e104ea59"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PublicReleaseTest(unittest.TestCase):
    def test_public_tree_audit_passes(self):
        audit = load_module("audit_public_tree", SCRIPTS / "audit_public_tree.py")
        result = audit.scan(ROOT)
        self.assertEqual(result["status"], "passed", result["findings"])

    def test_release_source_manifest_is_promoted(self):
        manifest = json.loads(
            (ROOT / "release" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["status"], "published")
        self.assertEqual(manifest["version"], VERSION)
        self.assertEqual(
            manifest["formalArtifact"]["sha256"],
            OFFICIAL_SHA,
        )
        self.assertEqual(manifest["distribution"]["skillhub"]["status"], "approved")
        self.assertEqual(manifest["distribution"]["workbuddy"]["version"], VERSION)
        self.assertFalse(manifest["publishGate"]["githubUploadAllowed"])

    def test_build_is_reproducible_and_manifest_verifies(self):
        with tempfile.TemporaryDirectory(prefix="fcx-public-build-a-") as left_raw:
            with tempfile.TemporaryDirectory(prefix="fcx-public-build-b-") as right_raw:
                left = Path(left_raw)
                right = Path(right_raw)
                command = [
                    sys.executable,
                    str(SCRIPTS / "build_release.py"),
                    "--version",
                    VERSION,
                ]
                subprocess.run(
                    command + ["--out-dir", str(left)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    command + ["--out-dir", str(right)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

                left_files = sorted(path.name for path in left.iterdir())
                right_files = sorted(path.name for path in right.iterdir())
                self.assertEqual(left_files, right_files)
                for name in left_files:
                    self.assertEqual(
                        sha256(left / name),
                        sha256(right / name),
                        name,
                    )

                verification = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "verify_release.py"),
                        "manifest",
                        "--manifest",
                        str(left / "release-manifest.json"),
                        "--asset-dir",
                        str(left),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                result = json.loads(verification.stdout)
                self.assertEqual(result["status"], "verified")
                self.assertEqual(result["releaseStatus"], "awaiting_promote")
                self.assertEqual(len(result["artifacts"]), 6)

                complete = next(
                    path
                    for path in left.iterdir()
                    if path.name.startswith("fact-check-x-complete-")
                )
                dry_run = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "install_skill.py"),
                        "--source",
                        str(complete),
                        "--target",
                        "codex",
                        "--dry-run",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                install_result = json.loads(dry_run.stdout)
                self.assertEqual(install_result["status"], "dry_run")
                self.assertEqual(
                    install_result["skill"], "fact-check-x-complete"
                )

    def test_skillhub_package_is_market_clean_and_reproducible(self):
        with tempfile.TemporaryDirectory(prefix="fcx-skillhub-a-") as left_raw:
            with tempfile.TemporaryDirectory(prefix="fcx-skillhub-b-") as right_raw:
                left = Path(left_raw)
                right = Path(right_raw)
                for target in (left, right):
                    subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPTS / "build_release.py"),
                            "--version",
                            VERSION,
                            "--out-dir",
                            str(target),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    complete = target / f"fact-check-x-complete-v{VERSION}.zip"
                    subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPTS / "build_skillhub_package.py"),
                            "--source",
                            str(complete),
                            "--output",
                            str(target / f"fact-check-x-skillhub-v{VERSION}.zip"),
                            "--version",
                            VERSION,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )

                left_package = left / f"fact-check-x-skillhub-v{VERSION}.zip"
                right_package = right / f"fact-check-x-skillhub-v{VERSION}.zip"
                self.assertEqual(sha256(left_package), sha256(right_package))
                with zipfile.ZipFile(left_package) as archive:
                    names = archive.namelist()
                    skill = archive.read(
                        "fact-check-x-complete/SKILL.md"
                    ).decode("utf-8")
                self.assertFalse(
                    any(Path(name).name in {"LICENSE", "NOTICE"} for name in names)
                )
                self.assertNotIn("](references/", skill)
                self.assertNotIn("V8", skill)
                for platform in (
                    "深知晓（深度研究）",
                    "豆包",
                    "腾讯元宝",
                    "DeepSeek",
                    "通义千问",
                ):
                    self.assertIn(platform, skill)


if __name__ == "__main__":
    unittest.main()
