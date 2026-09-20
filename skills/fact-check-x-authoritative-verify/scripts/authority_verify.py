#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common import SkillError, clipped, dump_json, load_json, now_iso


DKNOW_OFFICIAL_PLATFORMS = {"dknowc-chat", "dknowc-deep-research"}
OFFICIAL_EXEMPT_MODES = {"dknow_exempt", "gov_exempt"}
QUESTION_TOPIC_GROUPS = {
    "threshold": ("比例", "占比", "门槛", "额度", "金额", "上限", "下限", "标准"),
    "incentive": ("奖励", "补贴", "资助", "扶持"),
    "procedure": ("流程", "步骤", "材料", "办理", "申报"),
    "contact": ("电话", "咨询", "地址", "窗口"),
    "timing": ("时间", "期限", "时限", "日期"),
    "eligibility": ("条件", "资格", "对象", "范围"),
    "penalty": ("违法", "处罚", "罚款", "责任"),
}
BOILERPLATE_CUES = (
    "首页",
    "网站地图",
    "联系我们",
    "主办单位",
    "承办单位",
    "ICP备",
    "公安备案",
    "无障碍",
    "适老版",
    "返回顶部",
    "上一篇",
    "下一篇",
)
SUBSTANTIVE_POLICY_CUES = (
    "规定",
    "要求",
    "条件",
    "标准",
    "不低于",
    "不超过",
    "不得",
    "应当",
    "申请",
    "认定",
    "补贴",
    "奖励",
    "比例",
    "额度",
    "期限",
    "咨询电话",
)
CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CHINESE_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
CHINESE_LARGE_UNITS = {"万": 10000, "亿": 100000000}
CITY_SCOPE_TERMS = (
    "深圳",
    "北京",
    "上海",
    "广州",
    "天津",
    "重庆",
    "武汉",
    "成都",
    "杭州",
    "南京",
)


def is_government_url(url: object) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "gov.cn" or host.endswith(".gov.cn")


def anchor_source_policy(anchor: dict) -> str:
    explicit = str(anchor.get("sourcePolicy") or "")
    if explicit:
        return explicit
    if (
        str(anchor.get("platform") or "") in DKNOW_OFFICIAL_PLATFORMS
        and anchor.get("trustedSearchUsed") is True
    ):
        return "dknow_official_reference"
    return ""


def topic_groups(value: object) -> set[str]:
    text = re.sub(r"\s+", "", str(value or ""))
    return {
        group
        for group, terms in QUESTION_TOPIC_GROUPS.items()
        if any(term in text for term in terms)
    }


def chinese_number_to_int(value: str) -> int | None:
    if not value:
        return None
    if all(char in CHINESE_DIGITS for char in value):
        return int("".join(str(CHINESE_DIGITS[char]) for char in value))
    total = section = number = 0
    seen = False
    for char in value:
        if char in CHINESE_DIGITS:
            number = CHINESE_DIGITS[char]
            seen = True
        elif char in CHINESE_SMALL_UNITS:
            section += (number or 1) * CHINESE_SMALL_UNITS[char]
            number = 0
            seen = True
        elif char in CHINESE_LARGE_UNITS:
            section += number
            total += (section or 1) * CHINESE_LARGE_UNITS[char]
            section = number = 0
            seen = True
        else:
            return None
    return total + section + number if seen else None


def canonicalize_policy_expression(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "")).replace(",", "")
    text = re.sub(
        r"百分之([零〇一二两三四五六七八九十百千万亿]+)",
        lambda match: f"{chinese_number_to_int(match.group(1))}%"
        if chinese_number_to_int(match.group(1)) is not None
        else match.group(0),
        text,
    )
    text = re.sub(
        r"([零〇一二两三四五六七八九十百千]+)"
        r"(?=(?:元|万元|亿元|年|个月|月|日|天|人|家|件|次|岁|平方米))",
        lambda match: str(chinese_number_to_int(match.group(1)))
        if chinese_number_to_int(match.group(1)) is not None
        else match.group(0),
        text,
    )
    text = re.sub(r"(\d+(?:\.\d+)?)元/(?:每)?人/(?:每)?月", r"\1元每人每月", text)
    text = text.replace("每月每人", "每人每月")
    for variant in (
        "最高可提取额度",
        "最高提取额度",
        "最高可提取",
        "最高提取",
        "可提取额度",
        "提取限额",
    ):
        text = text.replace(variant, "提取额度")
    return text


def semantic_overlap_score(claim: str, evidence: str) -> float:
    grams = {
        claim[index : index + 2]
        for index in range(max(1, len(claim) - 1))
        if len(claim[index : index + 2]) == 2
    }
    return sum(gram in evidence for gram in grams) / max(1, len(grams))


def looks_like_navigation_or_footer(value: object) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    cue_count = sum(cue in text for cue in BOILERPLATE_CUES)
    substantive_count = sum(cue in text for cue in SUBSTANTIVE_POLICY_CUES)
    return len(text) <= 500 and cue_count >= 2 and substantive_count == 0


def geographic_scopes(value: object) -> set[str]:
    text = re.sub(r"\s+", "", str(value or ""))
    scopes = {term for term in CITY_SCOPE_TERMS if term in text}
    for match in re.finditer(
        r"([\u4e00-\u9fff]{2,6})(?:省|市|自治区|自治州|区|县|旗)", text
    ):
        scopes.add(match.group(1))
    return scopes


def semantic_claim_support(claim: object, evidence: object) -> bool:
    claim_text = canonicalize_policy_expression(claim)
    evidence_text = canonicalize_policy_expression(evidence)
    if (
        not claim_text
        or not evidence_text
        or looks_like_navigation_or_footer(evidence_text)
    ):
        return False
    if re.search(
        r"(?:可能|或许|据称|通常|一般情况下)", evidence_text
    ) and not re.search(r"(?:可能|或许|据称|通常|一般情况下)", claim_text):
        return False
    numeric = re.findall(r"\d+(?:\.\d+)?", claim_text)
    if any(value not in evidence_text for value in numeric):
        return False
    if any(
        re.search(
            rf"(?:并非|不是|不再是|错误(?:地|为)?|已取消).{{0,8}}{re.escape(value)}"
            rf"|{re.escape(value)}.{{0,8}}(?:不适用|已取消|错误)",
            evidence_text,
        )
        for value in numeric
    ):
        return False
    claim_topics = topic_groups(claim_text)
    evidence_topics = topic_groups(evidence_text)
    if claim_topics and evidence_topics and not (claim_topics & evidence_topics):
        return False
    claim_scopes = geographic_scopes(claim_text)
    evidence_scopes = geographic_scopes(evidence_text)
    if claim_scopes and evidence_scopes and not (claim_scopes & evidence_scopes):
        return False
    coverage = semantic_overlap_score(claim_text, evidence_text)
    longest = max(
        (
            block.size
            for block in difflib.SequenceMatcher(
                None, claim_text, evidence_text, autojunk=False
            ).get_matching_blocks()
        ),
        default=0,
    )
    if numeric:
        return coverage >= 0.28 and longest >= 5
    return coverage >= 0.36 and longest >= 7


def trusted_search_timeout_seconds() -> float:
    raw = os.getenv("FACTCHECK_TRUSTED_SEARCH_TIMEOUT_SECONDS", "90").strip()
    try:
        return max(10.0, min(float(raw), 300.0))
    except ValueError:
        return 90.0


def trusted_search_attempt_limit() -> int:
    raw = os.getenv("FACTCHECK_TRUSTED_SEARCH_ATTEMPTS", "3").strip()
    try:
        return max(1, min(int(raw), 3))
    except ValueError:
        return 3


def trusted_search_ssl_context() -> ssl.SSLContext:
    candidates: list[str] = []
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    defaults = ssl.get_default_verify_paths()
    candidates.extend(
        path
        for path in (
            defaults.cafile,
            "/etc/ssl/cert.pem",
            "/opt/homebrew/etc/openssl@3/cert.pem",
            "/usr/local/etc/openssl@3/cert.pem",
        )
        if path
    )
    for candidate in dict.fromkeys(candidates):
        if Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def validate_request(request: dict) -> None:
    if (
        not isinstance(request, dict)
        or request.get("schemaVersion") != "fact-check-x/authority-request@1"
    ):
        raise SkillError("请求必须使用 fact-check-x/authority-request@1")
    request_id = str(request.get("requestId") or "").strip()
    point = request.get("knowledgePoint") or {}
    if (
        not request_id
        or point.get("id") != request_id
        or not str(point.get("description") or "").strip()
    ):
        raise SkillError("requestId 与单知识点对象不一致")
    payload = request.get("cloudPayload")
    if not isinstance(payload, dict) or set(payload) - {
        "title",
        "knowledgePoint",
        "differingClaims",
    }:
        raise SkillError(
            "cloudPayload 只能包含 title、knowledgePoint 和可选 differingClaims"
        )
    if (
        payload.get("title") != request.get("title")
        or (payload.get("knowledgePoint") or {}).get("id") != request_id
    ):
        raise SkillError("cloudPayload 必须对应当前总标题和唯一知识点")
    differing = payload.get("differingClaims")
    status = request.get("comparisonStatus")
    if differing is not None and status not in (
        "conflict",
        "partial",
        "mostly_consensus",
    ):
        raise SkillError(
            "只有 conflict、partial 或 mostly_consensus 才能上传 differingClaims"
        )
    if differing is not None:
        values = {
            str(item.get("claim") or "").strip()
            for item in differing
            if isinstance(item, dict)
        }
        if len(values - {""}) < 2:
            raise SkillError("differingClaims 必须包含至少两种不同主张")


def parse_articles(data: dict, limit: int) -> list[dict]:
    articles = ((data.get("content") or {}).get("data") or {}).get("检索文章") or []
    output = []
    for index, article in enumerate(articles[:limit], 1):
        full = str(article.get("全文") or "").strip()
        segments = "\n".join(
            str(segment.get("内容") or "").strip()
            for segment in article.get("段落") or []
            if isinstance(segment, dict) and str(segment.get("内容") or "").strip()
        )
        body = (
            (segments + "\n" + full).strip()
            if full and segments and segments not in full
            else (full or segments)
        )
        if body:
            output.append(
                {
                    "id": f"E{index}",
                    "title": clipped(article.get("文章标题"), 240),
                    "url": str(article.get("源网址") or ""),
                    "date": str(article.get("发布日期") or ""),
                    "body": clipped(body, 6000),
                }
            )
    return output


def build_query(payload: dict) -> str:
    point = payload.get("knowledgePoint") or {}
    parts = [
        str(payload.get("title") or "").strip(),
        str(point.get("description") or "").strip(),
    ]
    differing = payload.get("differingClaims")
    if differing:
        parts.append(
            "；".join(
                f"{item.get('platform')}：{item.get('claim')}" for item in differing
            )
        )
    return clipped(" ".join(part for part in parts if part), 500)


def trusted_search(query: str, service_area: str, limit: int) -> dict:
    key = os.getenv("TRUSTED_SEARCH_KEY", "").strip()
    if not key:
        raise SkillError("未配置 TRUSTED_SEARCH_KEY")
    endpoint = os.getenv(
        "FACTCHECK_TRUSTED_SEARCH_URL", "https://open.dknowc.cn/dependable/search"
    ).strip()
    payload: dict[str, Any] = {
        "query": query,
        "segmentCount": 10,
        "simplified": True,
        "return_full_content": True,
    }
    if service_area:
        payload["service_area"] = [service_area]
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "api-key": key},
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=trusted_search_timeout_seconds(),
            context=trusted_search_ssl_context(),
        ) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {
            "status": "service_error",
            "error": f"HTTP {exc.code}: {detail}",
            "evidence": [],
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"status": "service_error", "error": str(exc), "evidence": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "status": "service_error",
            "error": f"响应不是有效 JSON: {exc}",
            "evidence": [],
        }
    code = data.get("code") if isinstance(data, dict) else None
    if not isinstance(data, dict) or status != 200 or code not in (None, 0, 200):
        return {
            "status": "service_error",
            "error": str(data.get("msg") if isinstance(data, dict) else "异常响应"),
            "evidence": [],
        }
    evidence = parse_articles(data, limit)
    return {
        "status": "verified" if evidence else "no_evidence",
        "error": "",
        "evidence": evidence,
    }


def fixture_search(fixture: object) -> dict:
    if isinstance(fixture, dict) and fixture.get("delayMs"):
        time.sleep(max(0, min(float(fixture["delayMs"]), 2000)) / 1000)
    if isinstance(fixture, dict) and fixture.get("status") == "service_error":
        return {
            "status": "service_error",
            "error": str(fixture.get("error") or "测试服务异常"),
            "evidence": [],
        }
    raw = (
        fixture
        if isinstance(fixture, list)
        else (fixture.get("evidence") if isinstance(fixture, dict) else [])
    )
    evidence = []
    for index, item in enumerate(raw or [], 1):
        evidence.append(
            {
                "id": str(item.get("id") or f"E{index}"),
                "title": clipped(item.get("title"), 240),
                "url": str(item.get("url") or ""),
                "date": str(item.get("date") or ""),
                "body": clipped(item.get("body"), 6000),
            }
        )
    return {
        "status": "verified" if evidence else "no_evidence",
        "error": "",
        "evidence": evidence,
    }


def anchor_evidence(anchor: dict) -> list[dict]:
    output = []
    for index, item in enumerate(anchor.get("evidence") or [], 1):
        output.append(
            {
                "id": str(item.get("id") or f"E{index}"),
                "title": str(item.get("title") or "已有官方依据"),
                "url": str(item.get("url") or ""),
                "date": "",
                "body": str(item.get("body") or item.get("excerpt") or ""),
                "contentAcquisition": str(item.get("contentAcquisition") or ""),
                "sameMaterialVerified": item.get("sameMaterialVerified") is True,
                "originAttributionStatus": str(
                    item.get("originAttributionStatus") or ""
                ),
                "platformUrl": str(item.get("platformUrl") or ""),
                "zone": str(item.get("zone") or ""),
                "platformTrustSource": str(item.get("platformTrustSource") or ""),
                "sourcePolicy": str(item.get("sourcePolicy") or ""),
            }
        )
    if not output:
        raise SkillError("eligible=true 的官方材料锚点缺少官方证据")
    return output


def valid_official_anchor(request: dict) -> bool:
    anchor = request.get("trustedAnchor") or {}
    anchor_platform = str(anchor.get("platform") or "")
    source_policy = anchor_source_policy(anchor)
    claim = (request.get("claims") or {}).get(anchor_platform) or {}
    if not (
        anchor.get("eligible")
        and claim.get("covered")
        and claim.get("faithfulness") == "supported"
        and claim.get("sourceLevel") in {"official", "dknow_trusted_search_official"}
    ):
        return False
    if source_policy == "dknow_official_reference":
        if (
            anchor_platform not in DKNOW_OFFICIAL_PLATFORMS
            or anchor.get("trustedSearchUsed") is not True
        ):
            return False
    elif source_policy == "gov_cn_reference":
        if anchor.get("trustedSearchUsed") is not False:
            return False
    else:
        return False
    evidence = anchor.get("evidence") or []
    if not evidence:
        return False
    for item in evidence:
        parsed = urlparse(str(item.get("url") or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        if (
            not isinstance(item.get("referenceIndex"), int)
            or item["referenceIndex"] < 1
        ):
            return False
        if not str(item.get("excerpt") or "").strip():
            return False
        if not str(item.get("body") or item.get("excerpt") or "").strip():
            return False
        item_policy = str(item.get("sourcePolicy") or source_policy)
        if item_policy == "gov_cn_reference":
            if not is_government_url(item.get("url")):
                return False
            continue
        if item_policy != "dknow_official_reference":
            return False
        candidate_urls = [
            str(item.get("url") or ""),
            str(item.get("platformUrl") or ""),
        ]
        internal = (
            any(
                (
                    (
                        (urlparse(url).hostname or "").lower() == "dknowc.cn"
                        or (urlparse(url).hostname or "").lower().endswith(".dknowc.cn")
                    )
                    and (
                        "/wlcb/shenzhi-policy/" in (urlparse(url).path or "").lower()
                        or "/DT_DATA/" in url.upper()
                    )
                )
                for url in candidate_urls
            )
            or str(item.get("zone") or "").strip().upper() == "DT_DATA"
        )
        internal = (
            internal and item.get("platformTrustSource") == "dknow_reference_capture"
        )
        captured_by_dknow = item.get("platformTrustSource") == "dknow_reference_capture"
        hydrated = (
            item.get("contentAcquisition") == "trusted_search_full_content"
            and item.get("sameMaterialVerified") is True
            and item.get("originAttributionStatus")
            in {"trusted_search_official_url", "trusted_search_no_source_url"}
        )
        if not (internal or captured_by_dknow or hydrated):
            return False
    claim_evidence_map = anchor.get("claimEvidenceMap") or {}
    if claim_evidence_map:
        if not isinstance(claim_evidence_map, dict):
            return False
        evidence_ids = {str(item.get("id") or "") for item in evidence}
        claims = request.get("claims") or {}
        for platform, ids in claim_evidence_map.items():
            claim_item = claims.get(platform) or {}
            if not (
                isinstance(ids, list)
                and ids
                and all(
                    isinstance(value, str) and value in evidence_ids for value in ids
                )
                and claim_item.get("covered")
                and claim_item.get("faithfulness") == "supported"
                and claim_item.get("sourceLevel")
                in {"official", "dknow_trusted_search_official"}
            ):
                return False
            selected = [
                {
                    "id": str(item.get("id") or f"A{index}"),
                    "body": str(item.get("body") or item.get("excerpt") or ""),
                }
                for index, item in enumerate(evidence, 1)
            ]
            if not evidence_supports_claim(claim_item, selected, ids):
                return False
        return anchor_platform in claim_evidence_map
    selected = [
        {
            "id": str(item.get("id") or f"A{index}"),
            "body": str(item.get("body") or item.get("excerpt") or ""),
        }
        for index, item in enumerate(evidence, 1)
    ]
    ids = [item["id"] for item in selected]
    if not evidence_supports_claim(claim, selected, ids):
        return False
    if not evidence_supports_claim(
        {"claim": str(anchor.get("officialAnswer") or "")}, selected, ids
    ):
        return False
    return True


def valid_trusted_anchor(request: dict) -> bool:
    return valid_official_anchor(request)


def acquire(
    request: dict, service_area: str = "", limit: int = 6, fixture: object | None = None
) -> dict:
    validate_request(request)
    anchor = request.get("trustedAnchor") or {}
    query = build_query(request["cloudPayload"])
    covered_claims = [
        claim
        for claim in (request.get("claims") or {}).values()
        if claim.get("covered")
    ]
    recommendation_only = bool(covered_claims) and all(
        claim.get("claimType") == "recommendation" for claim in covered_claims
    )
    if recommendation_only:
        result = {"status": "no_evidence", "error": "", "evidence": []}
        mode = "recommendation_not_applicable"
        count = 0
        attempt_count = 0
    elif valid_official_anchor(request):
        result = {
            "status": "verified",
            "error": "",
            "evidence": anchor_evidence(anchor),
        }
        mode = (
            "dknow_exempt"
            if anchor_source_policy(anchor) == "dknow_official_reference"
            else "gov_exempt"
        )
        count = 0
        attempt_count = 0
    else:
        attempt_limit = 1 if fixture is not None else trusted_search_attempt_limit()
        result = {
            "status": "service_error",
            "error": "可信搜索尚未执行",
            "evidence": [],
        }
        attempt_count = 0
        for attempt in range(attempt_limit):
            attempt_count += 1
            result = (
                fixture_search(fixture)
                if fixture is not None
                else trusted_search(query, service_area, limit)
            )
            if result.get("status") != "service_error":
                break
            if attempt + 1 < attempt_limit:
                time.sleep(0.5 * (2**attempt))
        mode = "trusted_search"
        count = 1
    return {
        "schemaVersion": "fact-check-x/authority-evidence@1",
        "requestId": request["requestId"],
        "createdAt": now_iso(),
        "status": result["status"],
        "searchMode": mode,
        "requestCount": count,
        "attemptCount": attempt_count,
        "query": query,
        "serviceArea": service_area,
        "error": result.get("error", ""),
        "evidence": result.get("evidence") or [],
    }


def normalize_verdict(item: dict, claim: dict, evidence_ids: set[str]) -> dict:
    verdict = item.get("verdict")
    if verdict not in ("supported", "contradicted", "insufficient"):
        verdict = "insufficient"
    ids = [
        evidence_id
        for evidence_id in item.get("evidenceIds") or []
        if evidence_id in evidence_ids
    ]
    if verdict in ("supported", "contradicted") and not ids:
        verdict = "insufficient"
    if claim.get("claimType") == "recommendation" and verdict != "contradicted":
        category = "recommendation"
    elif verdict == "supported":
        if claim.get("faithfulness") == "supported" and claim.get("sourceLevel") in (
            "official",
            "dknow_trusted_search_official",
        ):
            category = "direct_accurate"
        elif (
            claim.get("faithfulness") == "supported"
            and claim.get("sourceLevel") == "nonofficial"
        ):
            category = "indirect_accurate"
        else:
            category = "coincidental"
    elif verdict == "contradicted":
        category = "misleading"
    else:
        category = "unverified"
    return {
        "verdict": verdict,
        "category": category,
        "reason": clipped(item.get("reason"), 600),
        "evidenceIds": ids,
    }


def evidence_supports_claim(
    claim: dict, evidence_items: list[dict], ids: list[str]
) -> bool:
    selected = "\n".join(
        str(item.get("body") or "")
        for item in evidence_items
        if str(item.get("id")) in set(ids)
    )
    claim_text = str(claim.get("claim") or "")
    if semantic_claim_support(claim_text, selected):
        return True
    premise = " ".join(
        str(item.get("excerpt") or "") for item in claim.get("evidence") or []
    )
    return bool(premise.strip()) and semantic_claim_support(premise, selected)


def validate_assessment(request: dict, evidence: dict, assessment: dict) -> None:
    if evidence.get("status") != "verified":
        return
    anchor_mode = evidence.get("searchMode") in OFFICIAL_EXEMPT_MODES
    anchor = request.get("trustedAnchor") or {}
    anchor_platform = str(anchor.get("platform") or "")
    claim_evidence_map = anchor.get("claimEvidenceMap") or {}
    anchored_platforms = set(claim_evidence_map) or (
        {anchor_platform} if anchor_platform else set()
    )
    if anchor_mode:
        anchor = request.get("trustedAnchor") or {}
        if not valid_official_anchor(request):
            raise SkillError("官方材料免查请求不再满足锚点条件")
        if evidence.get("evidence") != anchor_evidence(anchor):
            raise SkillError("官方材料免查证据包与可信锚点不一致")
    if not isinstance(assessment, dict) or not assessment:
        raise SkillError(
            "已取得权威证据，但缺少裁决文件；必须提供 authoritativeFinding 和 verdicts"
        )
    if "platformAssessment" in assessment or (
        "verdict" in assessment and "verdicts" not in assessment
    ):
        raise SkillError(
            "裁决文件结构错误：不接受 platformAssessment 或顶层 verdict；请使用 authoritativeFinding 和 verdicts"
        )
    finding = assessment.get("authoritativeFinding")
    if not isinstance(finding, str) or not finding.strip():
        raise SkillError("裁决文件缺少非空 authoritativeFinding")
    verdicts = assessment.get("verdicts")
    if not isinstance(verdicts, dict):
        raise SkillError("裁决文件的 verdicts 必须是按平台 ID 组织的对象")

    claims = request.get("claims") or {}
    unknown_platforms = set(verdicts) - set(claims)
    if unknown_platforms:
        raise SkillError(f"裁决文件包含未知平台：{sorted(unknown_platforms)}")
    evidence_ids = {str(item.get("id")) for item in evidence.get("evidence") or []}
    for platform, claim in claims.items():
        if not claim.get("covered"):
            continue
        item = verdicts.get(platform)
        if not isinstance(item, dict):
            raise SkillError(f"裁决文件缺少已覆盖平台 {platform} 的 verdicts 条目")
        verdict = item.get("verdict")
        if verdict not in ("supported", "contradicted", "insufficient"):
            raise SkillError(
                f"{platform} 的 verdict 必须是 supported、contradicted 或 insufficient"
            )
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise SkillError(f"{platform} 的裁决缺少非空 reason")
        ids = item.get("evidenceIds")
        if not isinstance(ids, list) or any(
            not isinstance(value, str) or not value.strip() for value in ids
        ):
            raise SkillError(f"{platform} 的 evidenceIds 必须是证据 ID 字符串数组")
        invalid_ids = set(ids) - evidence_ids
        if invalid_ids:
            raise SkillError(
                f"{platform} 引用了证据包中不存在的 evidenceIds：{sorted(invalid_ids)}"
            )
        if verdict in ("supported", "contradicted") and not ids:
            raise SkillError(f"{platform} 的 {verdict} 裁决必须至少引用一个 evidenceId")
        if anchor_mode and platform in anchored_platforms and verdict != "supported":
            raise SkillError(
                f"合法 trustedAnchor 已建立，{platform} 的平台自带官方原文已支持当前主张，必须裁决为 supported；"
                "请在本阶段重写 assessment"
            )
        if (
            verdict == "supported"
            and not anchor_mode
            and not evidence_supports_claim(claim, evidence.get("evidence") or [], ids)
        ):
            raise SkillError(f"{platform} 的 supported 裁决所引证据无法定位当前主张")


def finalize(request: dict, evidence: dict, assessment: dict) -> dict:
    validate_request(request)
    if evidence.get(
        "schemaVersion"
    ) != "fact-check-x/authority-evidence@1" or evidence.get(
        "requestId"
    ) != request.get("requestId"):
        raise SkillError("证据包与当前请求不一致")
    status = evidence.get("status")
    if status == "service_error":
        raise SkillError(
            "可信搜索服务在自动重试后仍然失败；这是技术故障，请保留当前证据包并重新执行 search-authority，"
            "不得转成人工事实复核"
        )
    validate_assessment(request, evidence, assessment)
    evidence_ids = {str(item.get("id")) for item in evidence.get("evidence") or []}
    verdicts = {}
    evidence_gaps = []
    resolved_count = 0
    anchor = request.get("trustedAnchor") or {}
    anchor_platform = str(anchor.get("platform") or "")
    anchored_claims = (
        anchor.get("claimEvidenceMap") or {anchor_platform: []}
        if evidence.get("searchMode") in OFFICIAL_EXEMPT_MODES and anchor_platform
        else {}
    )
    for pid, claim in (request.get("claims") or {}).items():
        if not claim.get("covered"):
            verdicts[pid] = {
                "verdict": "omitted",
                "category": "omitted",
                "reason": "该平台未覆盖此知识点。",
                "evidenceIds": [],
            }
        elif status == "no_evidence" and claim.get("claimType") == "recommendation":
            verdicts[pid] = {
                "verdict": "insufficient",
                "category": "recommendation",
                "reason": "这是纯操作建议，不属于事实真伪裁决；直接引用不适用，相关制度事实需另列知识点核验。",
                "evidenceIds": [],
            }
            resolved_count += 1
        elif status == "no_evidence":
            verdicts[pid] = {
                "verdict": "insufficient",
                "category": "unverified",
                "reason": "本次可信搜索未返回可用于裁决的权威材料，不能据此判定主张真假。",
                "evidenceIds": [],
            }
            evidence_gaps.append(
                {
                    "platform": pid,
                    "reason": "可信搜索结果为空；该主张保持证据不足，不进入确定答案或准确率分母",
                }
            )
        elif status == "verified":
            verdicts[pid] = normalize_verdict(
                ((assessment.get("verdicts") or {}).get(pid) or {}), claim, evidence_ids
            )
            if pid in anchored_claims and claim.get("claimType") != "recommendation":
                verdicts[pid]["verdict"] = "supported"
                verdicts[pid]["category"] = "direct_accurate"
                verdicts[pid]["evidenceIds"] = list(
                    anchored_claims.get(pid) or verdicts[pid].get("evidenceIds") or []
                )
                verdicts[pid]["reason"] = (
                    "该平台本次回答已附官方原文，且比较阶段已逐主张绑定并验证支持。"
                )
            if verdicts[pid]["category"] == "unverified":
                evidence_gaps.append(
                    {
                        "platform": pid,
                        "reason": "现有权威证据不足以裁决该平台主张；该项不进入确定答案或准确率分母",
                    }
                )
            else:
                resolved_count += 1
        else:
            raise SkillError(f"未知证据状态: {status}")
    covered_claims = [
        claim
        for claim in (request.get("claims") or {}).values()
        if claim.get("covered")
    ]
    recommendation_only = bool(covered_claims) and all(
        claim.get("claimType") == "recommendation" for claim in covered_claims
    )
    finding = (
        clipped(assessment.get("authoritativeFinding"), 1200)
        if status == "verified"
        else "该知识点为纯操作建议，不属于事实真伪裁决；直接引用和可信搜索均不适用。"
        if recommendation_only
        else "本次可信搜索未取得可用于裁决该知识点的权威证据；相关主张保持证据不足。"
    )
    resolution = (
        "insufficient_evidence"
        if resolved_count == 0
        else "partially_resolved"
        if evidence_gaps
        else "resolved"
    )
    return {
        "schemaVersion": "fact-check-x/authority-result@1",
        "requestId": request["requestId"],
        "createdAt": now_iso(),
        "status": "completed",
        "resolution": resolution,
        "searchStatus": status,
        "searchMode": evidence.get("searchMode"),
        "requestCount": evidence.get("requestCount"),
        "knowledgePoint": request.get("knowledgePoint"),
        "claims": request.get("claims") or {},
        "authoritativeFinding": finding,
        "evidence": evidence.get("evidence") or [],
        "verdicts": verdicts,
        "evidenceGaps": evidence_gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="单知识点可信搜索与权威核验。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search")
    search.add_argument("--request", required=True)
    search.add_argument("--output", required=True)
    search.add_argument("--service-area", default="")
    search.add_argument("--limit", type=int, default=6)
    search.add_argument("--fixture")
    final = subparsers.add_parser("finalize")
    final.add_argument("--request", required=True)
    final.add_argument("--evidence", required=True)
    final.add_argument("--assessment")
    final.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        request = load_json(args.request)
        if args.command == "search":
            fixture = load_json(args.fixture) if args.fixture else None
            result = acquire(
                request, args.service_area.strip(), max(1, min(args.limit, 10)), fixture
            )
        else:
            evidence = load_json(args.evidence)
            assessment = load_json(args.assessment) if args.assessment else {}
            result = finalize(request, evidence, assessment)
        dump_json(args.output, result)
        print(
            json.dumps(
                {
                    "status": result.get("status"),
                    "output": str(Path(args.output).resolve()),
                    "searchMode": result.get("searchMode"),
                    "requestCount": result.get("requestCount"),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (SkillError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
