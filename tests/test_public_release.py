from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
OFFICIAL_SHA = "007fe204cddff50a19ecfd1d82e3c0c52c21ef3ff4ee73a45b4e99f5303165b6"


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
        self.assertEqual(
            manifest["upstreamCandidate"]["complete"]["sha256"],
            OFFICIAL_SHA,
        )
        self.assertEqual(
            manifest["officialPromotion"]["completeSha256"],
            OFFICIAL_SHA,
        )
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
                    "1.0.0",
                    "--upstream-candidate-sha",
                    OFFICIAL_SHA,
                    "--official-complete-sha",
                    OFFICIAL_SHA,
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
                self.assertEqual(result["releaseStatus"], "promote_verified")
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


if __name__ == "__main__":
    unittest.main()
