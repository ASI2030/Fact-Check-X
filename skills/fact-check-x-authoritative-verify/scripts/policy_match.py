"""Deterministic equivalence for locating policy evidence, not truth adjudication."""
import re
from decimal import Decimal

NUMBER = r"(?<![\d.])\d+(?:\.\d+)?"
UNIT = r"(?:亿元|万元|元|年|个月|月|日|天|件|家|人|次(?!性)|岁|平方米|分|%)"


def normalize_policy_quantities(text: str) -> str:
    text = text.replace("≥", ">=").replace("≤", "<=").replace("％", "%").replace("＜", "<").replace("＞", ">")
    # Bare money shorthand is accepted only at a boundary, never in 万人/万件.
    text = re.sub(r"万\s+(?=元|人|件|家|次|平方米)", "万", text)
    text = re.sub(r"(\d)\s*万(?=$|\s|[，,。；;、/（(）)至到~～—–<>-])", r"\1万元", text)
    def contextual_money(match):
        before = match.string[max(0, match.start() - 16):match.start()]
        if re.search(r"营收|收入|投入|金额|奖励|补贴|资助|最高|最低", before):
            return match[0] + "元"
        return match[0]
    text = re.sub(rf"{NUMBER}万(?!元|人|件|家|次|岁|年|日|吨|米|平方米)", contextual_money, text)
    text = re.sub(rf"({NUMBER})(?:至|到|[-—–~～])({NUMBER})({UNIT})", r"\1\3至\2\3", text)
    def money(match):
        factor = {"亿": Decimal(100000000), "万": Decimal(10000), "": Decimal(1)}[match[2]]
        value = format(Decimal(match[1]) * factor, "f")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        return value + "元"
    text = re.sub(rf"({NUMBER})(亿|万|)元", money, text)
    for pattern, replacement in (
        (r"不低于|不少于|至少", ">="),
        (r"不超过|不高于|不多于|至多", "<="),
        (r"超过|高于|大于", ">"),
        (r"低于|小于|不足", "<"),
    ):
        text = re.sub(rf"(?:{pattern})(?=\d)", replacement, text)
    text = re.sub(rf"(?<![<>=])([<>])({NUMBER}{UNIT})[（(]含[）)]", r"\1=\2", text)
    text = re.sub(rf"({NUMBER}{UNIT})(?:[（(]含[）)])?(以上|以下)",
                  lambda m: (">=" if m[2] == "以上" else "<=") + m[1], text)
    # Stable abbreviations used in policy answers; do not strip qualifications.
    text = text.replace("公租房", "公共租赁住房").replace("研发", "研究开发")
    return text


def numeric_facts_supported(claim: str, evidence: str) -> bool:
    claim, evidence = normalize_policy_quantities(claim), normalize_policy_quantities(evidence)
    pattern = re.compile(rf"(?P<op>>=|<=|>|<)?(?P<value>{NUMBER})(?P<unit>{UNIT})?")
    evidence_facts = list(pattern.finditer(evidence))
    for fact in pattern.finditer(claim):
        matches = [item for item in evidence_facts
                   if Decimal(item['value']) == Decimal(fact['value'])
                   and (not fact['unit'] or item['unit'] == fact['unit'])]
        if not matches:
            return False
        if fact['op'] and not any(item['op'] == fact['op'] for item in matches):
            return False
    return True
