#!/usr/bin/env python3
"""Presence, support and independent correctness must not be conflated."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from knowledge_compare import normalize_claim, numeric_facts_supported, raw_evidence_supports_claim, semantic_claim_support


def main():
    answer = "深圳企业可获50万元奖励【2】。"
    platform = {"platform": "doubao", "answerMarkdown": answer, "references": [
        {"marker": "1", "url": "https://www.sz.gov.cn/one", "content": "其他政策"},
        {"marker": "2", "url": "https://www.sz.gov.cn/two", "content": "", "sourceAcquisitionStatus": "failed", "sourceAcquisitionError": "HTTP 404"},
    ]}
    raw = {"covered": True, "claim": "深圳企业可获50万元奖励", "answerExcerpt": answer, "citedReferenceIndexes": [1], "faithfulness": "supported"}
    claim = normalize_claim(raw, platform, "K1", "fact", [])
    assert claim["attachedReferenceIndexes"] == [2], claim
    assert claim["citedReferenceIndexes"] == []
    assert claim["faithfulness"] == "insufficient"
    assert claim["referenceBinding"] == "local"
    # Unsupported citation still exists; unknown model-declared citation is not adopted.
    platform["references"][1]["content"] = "深圳企业可获20万元奖励。"
    claim = normalize_claim(raw, platform, "K1", "fact", [])
    assert claim["attachedReferenceIndexes"] == [2]
    assert claim["faithfulness"] == "insufficient"
    platform["references"][1]["content"] = "深圳企业可获50万元奖励。"
    claim = normalize_claim(raw, platform, "K1", "fact", [])
    assert claim["faithfulness"] == "supported" and claim["citedReferenceIndexes"] == [2]
    raw["covered"] = False
    assert normalize_claim(raw, platform, "K1", "fact", [])["attachedReferenceIndexes"] == []
    for check in (semantic_claim_support, raw_evidence_supports_claim):
        assert not check("市级培育资助最低20万元最高300万元", "市级培育资助最低10万元最高300万元。文件2016年。")
        assert not check("市级培育资助20万元至300万元", "市级培育资助，备案号44030402001126，网站标识4403000016。")
        assert check("市级培育资助20万元至300万元", "市级培育资助最低20万元、最高300万元。")
    assert numeric_facts_supported("资助20-300万元", "资助20万元到300万元")
    assert not numeric_facts_supported("资助20万元", "资助120万元")
    assert not numeric_facts_supported("资助20万元", "资助20元")
    print("PASS citation presence and numeric evidence regressions")


if __name__ == "__main__":
    main()
