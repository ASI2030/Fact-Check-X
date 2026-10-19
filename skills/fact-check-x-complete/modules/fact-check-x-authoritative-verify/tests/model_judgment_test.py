import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from authority_verify import acquire, finalize, SkillError


def main():
    request = json.loads((ROOT / "tests/fixtures/K1-request.json").read_text())
    assessment = json.loads((ROOT / "tests/fixtures/K1-assessment.json").read_text())
    evidence = acquire(request)
    result = finalize(request, evidence, assessment)
    assert result["verdicts"]["dknowc-chat"]["verdict"] == "supported"
    revised = copy.deepcopy(assessment)
    revised["verdicts"]["dknowc-chat"] = {"verdict": "contradicted", "reason": "同一原文记载的年度不适用于当前问题，来源忠实不代表适用正确", "evidenceIds": ["A1"]}
    result = finalize(request, evidence, revised)
    assert result["verdicts"]["dknowc-chat"]["verdict"] == "contradicted"
    assert result["claims"]["dknowc-chat"]["faithfulness"] == "supported"
    for ids in ([], ["missing"]):
        bad = copy.deepcopy(revised)
        bad["verdicts"]["dknowc-chat"]["evidenceIds"] = ids
        try:
            finalize(request, evidence, bad)
        except SkillError:
            pass
        else:
            raise AssertionError("untraceable model decision accepted")
    bad = copy.deepcopy(evidence)
    bad["evidence"][0]["body"] = ""
    try:
        finalize(request, bad, assessment)
    except SkillError:
        pass
    else:
        raise AssertionError("missing source body accepted")
    # Normalization must not re-run semantic matching on selected evidence.
    ordinary = copy.deepcopy(request)
    ordinary["trustedAnchor"] = {"eligible": False}
    ordinary["claims"]["dknowc-chat"]["claim"] = "上浮后可提取2800元"
    material = copy.deepcopy(evidence)
    material.update(searchMode="trusted_search", requestCount=1)
    material["evidence"] = [{"id": "A1", "body": "基准2000元，可上浮40%。", "title": "额度说明", "url": "https://example.gov.cn/a"}]
    decided = copy.deepcopy(assessment)
    for value in decided["verdicts"].values():
        value.update(verdict="supported", reason="模型按同一对象、周期计算2000×1.4=2800。", evidenceIds=["A1"])
    assert finalize(ordinary, material, decided)["verdicts"]["dknowc-chat"]["verdict"] == "supported"
    print("PASS semantic authority decisions, same-source scope conflicts, arithmetic and provenance gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
