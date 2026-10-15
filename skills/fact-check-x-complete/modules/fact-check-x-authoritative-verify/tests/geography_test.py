import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import authority_verify as authority

assert authority.geographic_scopes("部分采纳各区、费用区间、各区均有奖励") == set()
assert authority.geographic_scopes("深圳市福田区") == {"深圳", "福田区"}
assert not authority.semantic_claim_support("深圳市补贴100元", "北京市补贴100元")
assert not authority.semantic_claim_support("广东省深圳市补贴100元", "广东省广州市补贴100元")
assert not authority.semantic_claim_support("深圳市福田区补贴100元", "深圳市南山区补贴100元")
assert authority.semantic_claim_support("各区均有高企认定奖励", "部分采纳，各区均有高企认定奖励")
long_body = "正文。" * 3000 + "末条：有效期五年。"
articles = authority.parse_articles({"content": {"data": {"检索文章": [{"全文": long_body}]}}}, 1)
assert articles[0]["body"] == long_body
claim = {"claim": "各区均有高企认定奖励资助，市级财政不再对同一事项重复资助"}
excerpt = "部分采纳。各区均有高企业认定奖励资助，市级财政不再对同一事项重复资助。"
body = "其他项目每年可能调整。" + excerpt
assert authority.evidence_supports_claim(claim, [{"id": "A1", "body": body, "excerpt": excerpt}], ["A1"])
assert not authority.evidence_supports_claim(claim, [{"id": "A1", "body": "无关内容", "excerpt": excerpt}], ["A1"])
assert not authority.evidence_supports_claim(claim, [{"id": "A1", "body": body, "excerpt": excerpt}], ["A2"])
assert not authority.evidence_supports_claim({"claim": "企业可领取补贴100元"}, [{
    "id": "A1", "body": "据称企业可领取补贴100元。", "excerpt": "企业可领取补贴100元。"
}], ["A1"])

request = json.loads((Path(__file__).parent / "fixtures/K2-request.json").read_text())
request["title"] = request["cloudPayload"]["title"] = "国家高新技术企业认定条件与深圳奖励"
request["knowledgePoint"]["description"] = "市级认定奖励"
assert authority.request_service_area(request) == "深圳市"
request["knowledgePoint"]["description"] = "国家高新技术企业认定条件"
assert authority.request_service_area(request) == ""
request["knowledgePoint"]["description"] = "企业研发费用占比"
assert authority.request_service_area(request) == ""
request["knowledgePoint"]["description"] = "深圳市与北京市补贴对比"
assert authority.request_service_area(request) == ""
request["knowledgePoint"]["description"] = "深圳市补贴"
request["trustedAnchor"] = {"eligible": False}
with patch.object(authority, "trusted_search", return_value={"status": "no_evidence", "evidence": []}) as search:
    assert authority.acquire(request)["serviceArea"] == "深圳市"
    assert search.call_args.args[1] == "深圳市"
    assert authority.acquire(request, service_area="广东省")["serviceArea"] == "广东省"
print("PASS controlled geography and request-scoped service_area")
