import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from knowledge_compare import SkillError, normalize, normalize_claim, normalize_role


def main():
    body = "多子女家庭每人每月2000元的提取上限可以上浮40%。"
    answer = "多子女家庭每人每月提取上限2800元【1】"
    platform = {"platform": "dknowc-chat", "answerMarkdown": answer,
                "references": [{"url": "https://example.test/policy", "marker": "1",
                                "content": body, "platformTrustSource": "dknow_reference_capture"}]}
    claim = {"covered": True, "claim": "多子女家庭每人每月提取上限2800元",
             "answerExcerpt": answer, "faithfulness": "supported", "citedReferenceIndexes": [1],
             "reason": "同一对象和周期，2000乘以1.4等于2800，原文可直接推出。",
             "evidence": [{"referenceIndex": 1, "excerpt": body}]}
    result = normalize_claim(claim, platform, "K1", "fact", [])
    assert result["faithfulness"] == "supported" and result["reason"] == claim["reason"]
    assert result["decisionSource"] == "carrier_model"
    assert result["attachedReferenceIndexes"] == [1]
    for decision in ("insufficient", "contradicted"):
        other = copy.deepcopy(claim)
        other.update(faithfulness=decision, reason="模型核对后发现适用年度或对象不同，不能从该材料推出本题结论。")
        assert normalize_claim(other, platform, "K1", "fact", [])["faithfulness"] == decision
    for key, value in (("answerExcerpt", "原答案不存在的文字"), ("evidence", []), ("citedReferenceIndexes", [99]), ("reason", "")):
        broken = copy.deepcopy(claim)
        broken[key] = value
        try:
            normalize_claim(broken, platform, "K1", "fact", [])
        except SkillError:
            pass
        else:
            raise AssertionError(key)
    forged = copy.deepcopy(claim)
    forged["evidence"][0]["excerpt"] = "原文直接写了2800元"
    try:
        normalize_claim(forged, platform, "K1", "fact", [])
    except SkillError:
        pass
    else:
        raise AssertionError("fabricated quote accepted")
    # Identical text does not auto-promote the model's explicit uncertainty.
    uncertain = copy.deepcopy(claim)
    uncertain.update(claim=body, faithfulness="insufficient", reason="该段事实未确认适用于本年度")
    assert normalize_claim(uncertain, platform, "K1", "fact", [])["faithfulness"] == "insufficient"
    assert normalize_role("申请条件", "含条件的补充提醒", "reference") == ("reference", "carrier_model")
    assert normalize_role("深圳公租房", "盐田区实例", "direct") == ("direct", "carrier_model")
    raw = {"coreQuestion": "公积金", "synthesisDraft": {"status": "unverified", "answer": "按原文计算的金额", "basisKnowledgePointIds": ["K1"]},
           "knowledgePoints": [{"id": "K1", "description": "提取上限", "role": "direct", "claimType": "fact", "core": True,
               "claims": {"dknowc-chat": claim}, "comparison": {"status": "single", "summary": "单平台"}, "trustedAnchor": {"eligible": False}}]}
    comparison = normalize(raw, {"schemaVersion": "1"}, "公积金", [platform])
    assert comparison["knowledgePoints"][0]["trustedAnchor"]["eligible"]
    assert len(comparison["knowledgePoints"][0]["trustedAnchor"]["evidence"]) == 1
    print("PASS model judgment, arithmetic inference, uncertainty, citation identity, real excerpts and role preservation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
