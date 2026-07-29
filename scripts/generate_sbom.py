#!/usr/bin/env python3
"""Generate deterministic SPDX and CycloneDX SBOMs from the locked npm graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = (
    ROOT
    / "skills"
    / "llm-answer-reference-compare"
    / "assets"
    / "tool"
    / "package-lock.json"
)


def load_components() -> list[dict[str, str | bool]]:
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    components = []
    for package_path, metadata in sorted(lock.get("packages", {}).items()):
        if not package_path.startswith("node_modules/"):
            continue
        name = package_path.removeprefix("node_modules/")
        version = str(metadata.get("version") or "")
        license_id = str(metadata.get("license") or "NOASSERTION")
        resolved = str(metadata.get("resolved") or "")
        components.append(
            {
                "name": name,
                "version": version,
                "license": license_id,
                "resolved": resolved,
                "optional": bool(metadata.get("optional", False)),
            }
        )
    return components


def purl(name: str, version: str) -> str:
    encoded_name = quote(name, safe="/")
    return f"pkg:npm/{encoded_name}@{version}"


def spdx_document(version: str, components: list[dict]) -> dict:
    namespace_suffix = hashlib.sha256(
        f"Fact-Check-X:{version}".encode("utf-8")
    ).hexdigest()
    packages = [
        {
            "name": "Fact-Check-X",
            "SPDXID": "SPDXRef-Package-Fact-Check-X",
            "versionInfo": version,
            "downloadLocation": "https://github.com/ASI2030/Fact-Check-X",
            "filesAnalyzed": False,
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "Copyright 2026 Fact-Check-X contributors",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": f"pkg:github/ASI2030/Fact-Check-X@{version}",
                }
            ],
        }
    ]
    relationships = []
    for index, item in enumerate(components, start=1):
        package_id = f"SPDXRef-Package-npm-{index}"
        package = {
            "name": item["name"],
            "SPDXID": package_id,
            "versionInfo": item["version"],
            "downloadLocation": item["resolved"] or "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": item["license"],
            "licenseDeclared": item["license"],
            "copyrightText": "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": purl(item["name"], item["version"]),
                }
            ],
        }
        packages.append(package)
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-Fact-Check-X",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
            }
        )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"Fact-Check-X-{version}",
        "documentNamespace": (
            "https://github.com/ASI2030/Fact-Check-X/sbom/"
            f"{namespace_suffix}"
        ),
        "creationInfo": {
            "created": "2026-07-29T00:00:00Z",
            "creators": ["Tool: Fact-Check-X-generate_sbom.py"],
        },
        "packages": packages,
        "relationships": relationships,
    }


def cyclonedx_document(version: str, components: list[dict]) -> dict:
    serial = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"https://github.com/ASI2030/Fact-Check-X/releases/tag/v{version}",
    )
    cdx_components = []
    for item in components:
        component = {
            "type": "library",
            "name": item["name"],
            "version": item["version"],
            "purl": purl(item["name"], item["version"]),
            "licenses": [{"license": {"id": item["license"]}}],
            "properties": [
                {
                    "name": "fact-check-x:optional",
                    "value": str(item["optional"]).lower(),
                }
            ],
        }
        if item["resolved"]:
            component["externalReferences"] = [
                {"type": "distribution", "url": item["resolved"]}
            ]
        cdx_components.append(component)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "timestamp": "2026-07-29T00:00:00Z",
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "Fact-Check-X generate_sbom.py",
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": "Fact-Check-X",
                "version": version,
                "licenses": [{"license": {"id": "Apache-2.0"}}],
                "purl": f"pkg:github/ASI2030/Fact-Check-X@{version}",
            },
        },
        "components": cdx_components,
    }


def generate(out_dir: Path, version: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    components = load_components()
    spdx_path = out_dir / "sbom.spdx.json"
    cdx_path = out_dir / "sbom.cdx.json"
    spdx_path.write_text(
        json.dumps(
            spdx_document(version, components),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    cdx_path.write_text(
        json.dumps(
            cyclonedx_document(version, components),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return spdx_path, cdx_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    spdx_path, cdx_path = generate(Path(args.out_dir).resolve(), args.version)
    print(
        json.dumps(
            {
                "status": "completed",
                "spdx": str(spdx_path),
                "cycloneDx": str(cdx_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
