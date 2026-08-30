#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str]) -> None:
    proc = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if proc.returncode:
        raise AssertionError(proc.stdout or proc.stderr)


def main() -> int:
    markers = ["1", "3", "8", "7", "6", "11", "4"]
    references = [
        {
            "title": f"来源 {marker}",
            "url": f"https://example.gov.cn/source/{marker}",
            "normalizedUrl": f"https://example.gov.cn/source/{marker}",
            "marker": marker,
            "answerContext": "深圳夫妻投靠入户需求结婚登记满两年。",
            "sourceAcquisitionStatus": "failed",
            "sourceAcquisitionError": "来源正文未取得",
        }
        for marker in markers
    ]
    results = {
        "schemaVersion": "1",
        "question": "深圳夫妻投靠入户需要什么条件？",
        "platforms": [{
            "platform": "deepseek",
            "label": "DeepSeek",
            "url": "https://chat.deepseek.com",
            "status": "success",
            "answerMarkdown": "需求结婚登记满两年【1】。",
            "references": references,
        }],
    }
    analysis = {
        "schemaVersion": "fact-check-x/comparison-analysis@1",
        "coreQuestion": results["question"],
        "synthesisDraft": {
            "status": "unverified",
            "answer": "待核验",
            "basisKnowledgePointIds": ["K1"],
        },
        "knowledgePoints": [{
            "id": "K1",
            "description": "结婚登记年限",
            "role": "direct",
            "core": True,
            "claims": {"deepseek": {
                "covered": True,
                "claim": "需求结婚登记满两年",
                "answerExcerpt": "需求结婚登记满两年【1】。",
                "citedReferenceIndexes": [1],
                "answerLevelReferenceIndexes": [],
                "faithfulness": "supported",
                "reason": "",
                "evidence": [{
                    "referenceIndex": 1,
                    "excerpt": "深圳夫妻投靠入户需求结婚登记满两年。",
                }],
            }},
            "comparison": {"status": "single", "summary": "单平台回答"},
            "trustedAnchor": {"eligible": False},
        }],
    }
    with tempfile.TemporaryDirectory(prefix="fcx-deepseek-source-") as temporary:
        output = Path(temporary)
        results_path = output / "results.json"
        analysis_path = output / "analysis.json"
        comparison_path = output / "comparison.json"
        task_path = output / "task.json"
        results_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        analysis_path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input", str(results_path),
            "--task-output", str(task_path),
        ])
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input", str(results_path),
            "--analysis", str(analysis_path),
            "--output", str(comparison_path),
        ])
        task = json.loads(task_path.read_text(encoding="utf-8"))
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        assert [item["marker"] for item in task["platforms"][0]["references"]] == markers
        assert all(not item["capturedText"] for item in task["platforms"][0]["references"])
        claim = comparison["knowledgePoints"][0]["claims"]["deepseek"]
        assert claim["faithfulness"] == "insufficient"
        assert claim["sourceLevel"] == "none"
        assert claim["evidence"] == []

    print("PASS DeepSeek 7 条回答上下文不伪装成来源正文")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
