#!/usr/bin/env python3
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
modules = []
for skill, name in (("fact-check-x-knowledge-compare", "knowledge_compare"), ("fact-check-x-authoritative-verify", "authority_verify")):
    scripts = root / skill / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(name, scripts / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    modules.append(module)

positive = [
    ("家庭人均年可支配收入≤5.4万元", "家庭人均年可支配收入54000元（含）以下"),
    ("大鹏新区首次/新迁入35万、重新认定10万", "大鹏新区对新认定和新迁入的国家高新技术企业，给予最高35万元的一次性奖励；对在认定有效期届满后重新认定的国家高新技术企业，给予最高10万元的一次性奖励。"),
    ("境内研发费用占全部研发费用≥60%", "企业在中国境内发生的研究开发费用总额占全部研究开发费用总额的比例不低于60%"),
    ("申请轮候公租房需同时满足住房、户籍、社保、收入财产、未享受保障优惠等条件", "申请轮候公共租赁住房，应当同时符合以下条件：住房、户籍、社保、收入财产以及未享受住房保障优惠条件。"),
    ("创业资助20至300万元", "创业资助20万元至300万元"),
]
negative = [
    ("家庭人均年可支配收入≤5.4万元", "家庭人均年可支配收入5400元（含）以下"),
    ("境内研发费用占全部研发费用≥60%", "境内研发费用占全部研发费用不超过60%"),
    ("创新能力评价综合评分≥70分", "创新能力评价综合评分超过70分"),
    ("企业所得税由25%降至15%", "企业所得税减按15%征收"),
    ("深圳企业奖励35万元", "武汉企业奖励35万元"),
    ("深圳南山区企业奖励35万元", "深圳福田区企业奖励35万元"),
    ("补贴20万元", "网站标识4403002016，2016年发布"),
    ("岗位补贴5万元", "岗位数量5万人"),
    ("公租房租金1.38元/㎡·月", "公租房租金13.8元/㎡·月"),
    ("创业补贴10万元", "创业补贴10万元已取消"),
]
for module in modules:
    for claim, body in positive:
        assert module.semantic_claim_support(claim, body), (module.__name__, claim)
    for claim, body in negative:
        assert not module.semantic_claim_support(claim, body), (module.__name__, claim)
    assert module.numeric_facts_supported("5.4万元", "54000元")
    assert not module.numeric_facts_supported("35万", "35万人")
    assert module.numeric_facts_supported("20至300万元", "20万元至300万元")
    assert module.canonicalize_policy_expression("35万人") == "35万人"
    assert module.canonicalize_policy_expression("5,000万元") == "50000000元"
    assert module.canonicalize_policy_expression("营收≥1000万高企，最高100万研发奖励") == "营收>=10000000元高企，最高1000000元研究开发奖励"
    assert module.canonicalize_policy_expression("新增1000万高企") == "新增1000万高企"
    sample = "收入≤5.4万元，资助20至300万元"
    normalized = module.canonicalize_policy_expression(sample)
    assert module.canonicalize_policy_expression(normalized) == normalized

verify = modules[1]
assert verify.evidence_supports_claim({"claim": positive[3][0]}, [
    {"id": "A1", "body": positive[3][1]},
    {"id": "A2", "body": "另一个项目通常可能适用"},
], ["A1", "A2"])
assert modules[0].claim_scope_overreach_reason("收入不超过5.4万元", "收入≤54000元") == ""
assert modules[0].claim_scope_overreach_reason("大鹏新区首次35万、重新认定10万", "大鹏新区\t35 万\t10 万") == ""
assert modules[0].claim_scope_overreach_reason("盐田区30万（以当年文件为准）", "盐田区\t30 万\t以当年文件为准") == ""
assert modules[0].claim_scope_overreach_reason("企业所得税由25%降至15%", "企业所得税减按15%征收")
print("PASS equivalent quantities, comparison boundaries, abbreviations, independent documents and negative controls")
