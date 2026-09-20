#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from common import SkillError, clipped, dump_json, load_json, now_iso


OFFICIAL_MEDIA = ("people.com.cn", "xinhuanet.com", "qstheory.cn", "gmw.cn")
DKNOW_OFFICIAL_PLATFORMS = {"dknowc-chat", "dknowc-deep-research"}
OFFICIAL_ORIGIN_KEYS = (
    "originUrl",
    "origin_url",
    "resourceUrl",
    "resource_url",
    "officialUrl",
    "official_url",
    "sourceUrl",
    "source_url",
)

QUESTION_TOPIC_GROUPS = {
    "threshold": ("比例", "占比", "门槛", "额度", "金额", "上限", "下限", "标准"),
    "incentive": ("奖励", "补贴", "资助", "扶持"),
    "procedure": ("流程", "步骤", "材料", "办理", "申报"),
    "contact": ("电话", "咨询", "地址", "窗口"),
    "timing": ("时间", "期限", "时限", "日期"),
    "eligibility": ("条件", "资格", "对象", "范围"),
    "penalty": ("违法", "处罚", "罚款", "责任"),
}

CITY_SCOPE_TERMS = ("深圳", "北京", "上海", "广州", "天津", "重庆", "武汉", "成都", "杭州", "南京")
SUPPLEMENTAL_CUES = ("附加条件", "另有条件", "计算口径", "指标定义", "旧规", "历史规定")

TASK_REFERENCE_PREVIEW_MAX_CHARS = 900
TASK_PLATFORM_REFERENCE_BUDGET = 16000

BOILERPLATE_CUES = (
    "首页", "网站地图", "联系我们", "主办单位", "承办单位", "ICP备",
    "公安备案", "无障碍", "适老版", "返回顶部", "上一篇", "下一篇",
)
SUBSTANTIVE_POLICY_CUES = (
    "规定", "要求", "条件", "标准", "不低于", "不超过", "不得", "应当",
    "申请", "认定", "补贴", "奖励", "比例", "额度", "期限", "咨询电话",
)
CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CHINESE_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
CHINESE_LARGE_UNITS = {"万": 10000, "亿": 100000000}


def topic_groups(value: object) -> set[str]:
    text = re.sub(r"\s+", "", str(value or ""))
    return {
        group
        for group, terms in QUESTION_TOPIC_GROUPS.items()
        if any(term in text for term in terms)
    }


def normalize_role(question: str, description: str, raw_role: object) -> tuple[str, str]:
    """Normalize answer scope against the user's requested subject and level."""
    role = raw_role if raw_role in ("direct", "reference") else "direct"
    compact_question = re.sub(r"\s+", "", question)
    compact_description = re.sub(r"\s+", "", description)
    asks_subregions = any(
        term in compact_question
        for term in ("各区", "区县", "不同区", "不同地区", "分别", "差异", "对比")
    )
    city_scoped = any(term in compact_question for term in CITY_SCOPE_TERMS)
    explicit_subregions = re.findall(
        r"[\u4e00-\u9fff]{2,6}(?:区|县|旗|街道|镇|乡)",
        compact_description,
    )
    if (
        city_scoped
        and not asks_subregions
        and (
            any(term in compact_description for term in ("各区", "区级", "各县", "县级", "街道"))
            or any(mention not in compact_question for mention in explicit_subregions)
        )
    ):
        return "reference", "subregion_outside_question_scope"
    national_scoped = any(term in compact_question for term in ("全国", "中国", "国家层面"))
    local_detail = bool(
        re.search(r"[\u4e00-\u9fff]{2,10}(?:省|市|自治区|自治州|县|区)(?:级|政策|奖励|补贴|标准|口径)", compact_description)
    )
    if national_scoped and not asks_subregions and local_detail:
        return "reference", "local_detail_outside_question_scope"
    if (
        national_scoped
        and not asks_subregions
        and any(term in compact_description and term not in compact_question for term in CITY_SCOPE_TERMS)
    ):
        return "reference", "local_detail_outside_question_scope"
    if any(term in compact_description for term in ("背景信息", "延伸信息", "相关提醒", "其他地区示例")):
        return "reference", "background_or_extension"
    if any(term in compact_description for term in SUPPLEMENTAL_CUES) and not any(
        term in compact_question for term in SUPPLEMENTAL_CUES
    ):
        return "reference", "background_or_extension"
    asks_requirements = any(
        term in compact_question
        for term in ("条件", "门槛", "要求", "资格", "须满足", "需要满足")
    )
    describes_requirement = bool(re.search(
        r"(?:须|应当|不得|不低于|不超过|未发生|达到|达标|拥有|属于|注册成立)",
        compact_description,
    ))
    if role == "reference" and asks_requirements and describes_requirement:
        return "direct", "requested_requirement"
    question_topics = topic_groups(compact_question)
    point_topics = topic_groups(compact_description)
    if question_topics and point_topics and not (question_topics & point_topics):
        return "reference", "adjacent_topic_not_requested"
    if role == "reference":
        return role, "model_reference"
    return role, "model_direct"


def normalize_claim_type(description: str, raw_type: object, raw_claims: object) -> str:
    claim_type = raw_type if raw_type in ("fact", "recommendation") else "fact"
    if claim_type != "recommendation":
        return claim_type
    text = " ".join(
        [description]
        + [
            str(item.get("claim") or "")
            for item in (raw_claims or {}).values()
            if isinstance(item, dict) and item.get("covered")
        ]
    )
    if re.search(r"\d", text) or any(
        term in text
        for term in ("不低于", "不得", "必须", "应当", "有效期", "办理时限", "咨询电话")
    ):
        return "fact"
    return claim_type


def is_official_url(url: object) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    if is_government_url(url) or any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_MEDIA):
        return True
    return False


def is_government_url(url: object) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "gov.cn" or host.endswith(".gov.cn")


def valid_http_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def verified_official_origin(reference: dict, platform_id: str = "") -> str:
    if reference.get("originAttributionStatus") == "trusted_search_no_source_url":
        return ""
    for key in OFFICIAL_ORIGIN_KEYS:
        candidate = str(reference.get(key) or "").strip()
        if candidate and (
            is_official_url(candidate)
            or (
                platform_id in DKNOW_OFFICIAL_PLATFORMS
                and reference.get("originAttributionStatus")
                == "trusted_search_official_url"
                and valid_http_url(candidate)
            )
        ):
            return candidate
    return ""


def is_dknow_trusted_reference(reference: dict, platform_id: str = "") -> bool:
    if platform_id not in DKNOW_OFFICIAL_PLATFORMS:
        return False
    # 两个深知平台的来源列表都由可信搜索提供。官网回链是
    # 可选的回溯信息，不是“官方来源”的判定前提。
    return bool(
        reference_text(reference)
        or str(reference.get("title") or "").strip()
        or str(reference.get("url") or "").strip()
    )


def has_trusted_dknow_provenance(reference: dict, platform_id: str = "") -> bool:
    return is_dknow_trusted_reference(reference, platform_id)


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


def normalize_chinese_numbers(text: str) -> str:
    text = re.sub(
        r"百分之([零〇一二两三四五六七八九十百千万亿]+)",
        lambda match: f"{chinese_number_to_int(match.group(1))}%"
        if chinese_number_to_int(match.group(1)) is not None else match.group(0),
        text,
    )
    pattern = re.compile(
        r"([零〇一二两三四五六七八九十百千]+)"
        r"(?=(?:元|万元|亿元|年|个月|月|日|天|人|家|件|次|岁|平方米))"
    )
    return pattern.sub(
        lambda match: str(chinese_number_to_int(match.group(1)))
        if chinese_number_to_int(match.group(1)) is not None else match.group(0),
        text,
    )


def canonicalize_policy_expression(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "")).replace(",", "")
    text = normalize_chinese_numbers(text)
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


def looks_like_navigation_or_footer(value: object) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    cue_count = sum(cue in text for cue in BOILERPLATE_CUES)
    substantive_count = sum(cue in text for cue in SUBSTANTIVE_POLICY_CUES)
    return len(text) <= 500 and cue_count >= 2 and substantive_count == 0


def longest_common_claim_span(claim: str, evidence: str) -> int:
    return max(
        (block.size for block in difflib.SequenceMatcher(None, claim, evidence, autojunk=False).get_matching_blocks()),
        default=0,
    )


def geographic_scopes(value: object) -> set[str]:
    text = re.sub(r"\s+", "", str(value or ""))
    scopes = {term for term in CITY_SCOPE_TERMS if term in text}
    for match in re.finditer(r"([\u4e00-\u9fff]{2,6})(?:省|市|自治区|自治州|区|县|旗)", text):
        name = match.group(1)
        if len(name) <= 6:
            scopes.add(name)
    return scopes


def claim_requires_explicit_absence_evidence(value: object) -> bool:
    text = canonicalize_policy_expression(value)
    policy_object = r"(?:认定)?(?:奖励|奖金|补贴|资助|政策|标准|名额|资格)"
    return bool(
        re.search(rf"(?:无|没有|未设|不设|不存在|已取消).{{0,12}}{policy_object}", text)
        or re.search(rf"{policy_object}.{{0,12}}(?:无|没有|未设|不设|不存在|已取消)", text)
        or re.search(r"(?:全部|一律|仅|只).{0,16}(?:在|由|属于|限于)", text)
    )


def has_explicit_absence_evidence(value: object) -> bool:
    text = canonicalize_policy_expression(value)
    policy_object = r"(?:认定)?(?:奖励|奖金|补贴|资助|政策|标准|名额|资格)"
    return bool(
        re.search(rf"(?:无|没有|未设|不设|不存在|已取消).{{0,12}}{policy_object}", text)
        or re.search(rf"{policy_object}.{{0,12}}(?:无|没有|未设|不设|不存在|已取消)", text)
        or re.search(r"(?:全部|一律|仅|只).{0,16}(?:在|由|属于|限于)", text)
    )


def quantified_fact_tokens(value: object) -> set[str]:
    text = canonicalize_policy_expression(value)
    return {
        re.sub(r"\s+", "", match.group(0))
        for match in re.finditer(
            r"\d+(?:\.\d+)?\s*(?:%|亿元|万元|元|年|个月|月|日|天|件|家|人|次(?!性)|岁|平方米|分)",
            text,
        )
    }


def claim_scope_overreach_reason(claim: object, answer_excerpt: object) -> str:
    """Return a deterministic structural error when a claim exceeds its excerpt.

    The comparison carrier may paraphrase prose, but it may not add a separate
    quantified fact or an absence/exclusivity conclusion that is not present in
    the answer excerpt bound to that knowledge point. Such a mismatch belongs
    to comparison analysis, not to the evaluated platform.
    """
    claim_text = canonicalize_policy_expression(claim)
    excerpt_text = canonicalize_policy_expression(answer_excerpt)
    if not claim_text or not excerpt_text:
        return ""
    missing_quantified = sorted(
        quantified_fact_tokens(claim_text) - quantified_fact_tokens(excerpt_text)
    )
    if missing_quantified:
        return "主张范围超出 answerExcerpt，缺少量化事实：" + "、".join(missing_quantified)
    if claim_requires_explicit_absence_evidence(claim_text) and not has_explicit_absence_evidence(excerpt_text):
        return "主张新增了 answerExcerpt 未表达的无政策、取消或排他性结论"
    return ""


def semantic_claim_support(claim: object, evidence: object) -> bool:
    claim_text = canonicalize_policy_expression(claim)
    evidence_text = canonicalize_policy_expression(evidence)
    if not claim_text or not evidence_text or looks_like_navigation_or_footer(evidence_text):
        return False
    if claim_requires_explicit_absence_evidence(claim_text) and not has_explicit_absence_evidence(evidence_text):
        return False
    if (
        re.search(r"(?:可能|或许|据称|通常|一般情况下)", evidence_text)
        and not re.search(r"(?:可能|或许|据称|通常|一般情况下)", claim_text)
    ):
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
    coverage, _, _ = semantic_overlap_score(claim_text, evidence_text)
    longest = longest_common_claim_span(claim_text, evidence_text)
    if numeric:
        return coverage >= 0.28 and longest >= 5
    return coverage >= 0.36 and longest >= 7


def text_supports_claim(claim: object, evidence: object) -> bool:
    return semantic_claim_support(claim, evidence)


def raw_evidence_supports_claim(claim: object, evidence: object) -> bool:
    """Validate model-selected excerpts before accepting them as evidence.

    Raw excerpts may be slightly hedged, so they retain the legacy semantic
    tolerance. They must still match the claim's numbers and topic, and an
    explicit contradiction is never accepted.
    """
    if text_supports_claim(claim, evidence):
        return True
    claim_text = canonicalize_policy_expression(claim)
    evidence_text = canonicalize_policy_expression(evidence)
    if not claim_text or not evidence_text:
        return False
    if claim_requires_explicit_absence_evidence(claim_text) and not has_explicit_absence_evidence(evidence_text):
        return False
    numeric = re.findall(r"\d+(?:\.\d+)?", claim_text)
    if numeric and not all(value in evidence_text for value in numeric):
        return False
    units = set(re.findall(
        r"\d+(?:\.\d+)?\s*(万元|元|年|月|日|天|件|家|人|次|岁|平方米|%)",
        claim_text,
    ))
    if any(unit not in evidence_text for unit in units):
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
    coverage, numeric_hits, _ = semantic_overlap_score(claim_text, evidence_text)
    return coverage >= 0.2 and (not numeric or numeric_hits == len(numeric))


def source_level(reference: dict, platform_id: str = "") -> str:
    url = str(reference.get("url") or "")
    host = (urlparse(url).hostname or "").lower()
    if is_dknow_trusted_reference(reference, platform_id):
        return "dknow_trusted_search_official"
    if is_official_url(url):
        return "official"
    return "nonofficial" if host else "none"


def official_anchor_policy(reference: dict, platform_id: str) -> str:
    if is_dknow_trusted_reference(reference, platform_id):
        return "dknow_official_reference"
    if is_government_url(reference.get("url")):
        return "gov_cn_reference"
    return ""


def reference_text(reference: dict) -> str:
    parts = []
    for key in ("snippet", "text", "content", "body"):
        if key == "snippet" and reference.get("snippetProvenance") == "answer_context":
            continue
        value = str(reference.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return "\n".join(parts)


def semantic_overlap_score(claim: str, evidence: str) -> tuple[float, int, int]:
    compact_claim = re.sub(r"\s+", "", claim)
    compact_evidence = re.sub(r"\s+", "", evidence)
    if not compact_claim or not compact_evidence:
        return (0.0, 0, 0)
    grams = {
        compact_claim[index:index + 2]
        for index in range(max(1, len(compact_claim) - 1))
        if len(compact_claim[index:index + 2]) == 2
    }
    overlap = sum(gram in compact_evidence for gram in grams)
    coverage = overlap / max(1, len(grams))
    numeric = re.findall(r"\d+(?:\.\d+)?", compact_claim.replace(",", ""))
    numeric_hits = sum(value in compact_evidence.replace(",", "") for value in numeric)
    return (coverage, numeric_hits, -len(evidence))


def evidence_windows(reference: dict, max_chars: int = 1800) -> list[str]:
    text = reference_text(reference)
    if not text:
        return []
    spans = [
        match.span()
        for match in re.finditer(r"[^。！？；!?\n]+(?:[。！？；!?]+|(?=\n|$))", text)
        if match.group(0).strip()
    ]
    candidates = []
    seen = set()
    for start_index, (start, _) in enumerate(spans):
        for end_index in range(start_index, min(len(spans), start_index + 6)):
            end = spans[end_index][1]
            candidate = text[start:end].strip()
            if not candidate or len(candidate) > max_chars:
                break
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
    for paragraph in text.splitlines():
        candidate = paragraph.strip()
        if candidate and len(candidate) <= max_chars and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def supporting_excerpt(reference: dict, claim: str) -> str:
    candidates = [
        candidate
        for candidate in evidence_windows(reference)
        if text_supports_claim(claim, candidate)
    ]
    if not candidates:
        return ""
    return max(candidates, key=lambda item: semantic_overlap_score(claim, item))


def marker_occurs(answer: str, marker: str, known_markers: set[str] | None = None) -> bool:
    marker = marker.strip()
    if not marker:
        return False
    escaped = re.escape(marker)
    if re.search(rf"(?:\[{escaped}\]|【{escaped}】|〔{escaped}〕)", answer):
        return True
    if marker.isdigit() and len(marker) <= 3:
        # Some answer UIs emit a complete short marker such as 113, while others
        # flatten adjacent one/two-digit markers into a trailing cluster such as
        # 123. If the whole cluster is a known marker, exact-marker semantics win;
        # otherwise retain the legacy cluster decomposition for one/two-digit refs.
        cluster_matches = re.finditer(
            r"(?<![\d:：])(\d{1,3})(?=[。！？；，、!?;)]|[.,](?!\d)|$)",
            answer,
        )
        clusters = []
        for match in cluster_matches:
            line_start = answer.rfind("\n", 0, match.start(1)) + 1
            if not answer[line_start:match.start(1)].strip():
                continue
            clusters.append(match.group(1))
        known = known_markers or set()
        for cluster in clusters:
            if cluster in known:
                if marker == cluster:
                    return True
                continue
            if len(marker) <= 2 and marker in cluster:
                return True
    return marker.isdigit() and len(marker) >= 5 and bool(re.search(rf"(?<!\d){escaped}(?!\d)", answer))


def inline_reference_indexes(text: str, references: list[dict]) -> set[int]:
    known_markers = {
        str(reference.get("marker") or "").strip()
        for reference in references
        if str(reference.get("marker") or "").strip()
    }
    return {
        index
        for index, reference in enumerate(references, 1)
        if marker_occurs(text, str(reference.get("marker") or ""), known_markers)
    }


def citation_contexts(text: str, marker: str) -> list[str]:
    marker = marker.strip()
    if not marker:
        return []
    escaped = re.escape(marker)
    pattern = re.compile(rf"(?:\[{escaped}\]|【{escaped}】|〔{escaped}〕)")
    contexts = []
    seen = set()
    for line in text.splitlines():
        candidate = line.strip()
        if candidate and pattern.search(candidate) and candidate not in seen:
            seen.add(candidate)
            contexts.append(candidate)
    if contexts:
        return contexts
    for match in re.finditer(r"[^。！？；!?\n]+(?:[。！？；!?]+|(?=\n|$))", text):
        candidate = match.group(0).strip()
        if candidate and pattern.search(candidate) and candidate not in seen:
            seen.add(candidate)
            contexts.append(candidate)
    return contexts


def claim_fragment_related(claim: str, fragment: str) -> bool:
    claim_numbers = set(re.findall(r"\d+(?:\.\d+)?", claim.replace(",", "")))
    fragment_numbers = set(re.findall(r"\d+(?:\.\d+)?", fragment.replace(",", "")))
    if claim_numbers and not (claim_numbers & fragment_numbers):
        return False
    coverage, _, _ = semantic_overlap_score(fragment, claim)
    return text_supports_claim(fragment, claim) or coverage >= 0.2


def locally_supported_claim_evidence(
    answer: str,
    reference: dict,
    claim: str,
) -> str:
    marker = str(reference.get("marker") or "").strip()
    related_contexts = []
    for context in citation_contexts(answer, marker):
        plain = re.sub(r"(?:\[\d+\]|【\d+】|〔\d+〕)", "", context).strip()
        if plain and claim_fragment_related(claim, plain):
            related_contexts.append(plain)
    if not related_contexts:
        return ""
    local_claim = "\n".join(related_contexts)
    return supporting_excerpt(reference, local_claim)


def declared_cluster_reference_indexes(
    text: str,
    references: list[dict],
    declared_indexes: list[int],
) -> set[int]:
    if len(declared_indexes) < 2:
        return set()
    markers = [
        str(references[index - 1].get("marker") or "").strip()
        for index in declared_indexes
    ]
    if any(not marker.isdigit() or len(marker) > 2 for marker in markers):
        return set()
    combined = "".join(markers)
    if not 2 <= len(combined) <= 3:
        return set()
    clusters = re.findall(
        r"(?<![\d\[【〔:：])(\d{2,3})(?=[。！？；，、!?;)]|[.,](?!\d)|$)",
        text,
    )
    return set(declared_indexes) if combined in clusters else set()


def citation_scope(reference: dict, answer: str) -> str:
    declared = str(reference.get("citationScope") or "").strip().lower()
    if declared in ("inline", "global", "inline_and_global"):
        return declared
    return "inline"


def validate_results(data: dict) -> tuple[str, list[dict]]:
    if not isinstance(data, dict) or data.get("schemaVersion") != "1":
        raise SkillError("results.json 必须使用 schemaVersion=1")
    question = str(data.get("question") or "").strip()
    if not question:
        raise SkillError("results.json 缺少 question")
    successful = []
    seen = set()
    for platform in data.get("platforms") or []:
        pid = str(platform.get("platform") or "").strip()
        if not pid or pid in seen:
            raise SkillError("平台标识必须非空且唯一")
        seen.add(pid)
        if platform.get("status") == "success" and str(platform.get("answerMarkdown") or "").strip():
            successful.append(platform)
    if len(successful) < 1:
        raise SkillError("1.1 至少需要一个成功且非空的平台回答")
    return question, successful


def task_reference_preview(answer: str, reference: dict, max_chars: int) -> str:
    """Return bounded verbatim evidence for analysis; full text stays in results.json."""
    full_text = reference_text(reference)
    if not full_text or max_chars <= 0:
        return ""
    if len(full_text) <= max_chars:
        return full_text

    candidates = []
    marker = str(reference.get("marker") or "").strip()
    for context in citation_contexts(answer, marker):
        plain = re.sub(r"(?:\[\d+\]|【\d+】|〔\d+〕)", "", context).strip()
        excerpt = supporting_excerpt(reference, plain)
        if excerpt and excerpt not in candidates:
            candidates.append(excerpt)

    ranked_windows = sorted(
        evidence_windows(reference, max_chars=max_chars),
        key=lambda item: semantic_overlap_score(answer, item),
        reverse=True,
    )
    candidates.extend(item for item in ranked_windows if item not in candidates)

    selected = []
    used = 0
    for candidate in candidates:
        if not candidate:
            continue
        if len(candidate) > max_chars:
            candidate = candidate[:max_chars]
        separator = 2 if selected else 0
        if used + separator + len(candidate) > max_chars:
            continue
        selected.append(candidate)
        used += separator + len(candidate)
        if used >= max_chars:
            break
    return "\n\n".join(selected) if selected else full_text[:max_chars]


def task_platform(platform: dict) -> dict:
    answer = str(platform.get("answerMarkdown") or "")
    references = []
    explicit = []
    raw_references = platform.get("references") or []
    preview_chars = min(
        TASK_REFERENCE_PREVIEW_MAX_CHARS,
        max(240, TASK_PLATFORM_REFERENCE_BUDGET // max(1, len(raw_references))),
    )
    known_markers = {
        str(reference.get("marker") or "").strip()
        for reference in platform.get("references") or []
        if str(reference.get("marker") or "").strip()
    }
    for index, reference in enumerate(raw_references, 1):
        marker = str(reference.get("marker") or "").strip()
        scope = citation_scope(reference, answer)
        if marker_occurs(answer, marker, known_markers):
            explicit.append(index)
        full_text = reference_text(reference)
        preview = task_reference_preview(answer, reference, preview_chars)
        normalized_reference = {
            "index": index,
            "title": str(reference.get("title") or reference.get("text") or reference.get("url") or ""),
            "originalUrl": str(reference.get("url") or ""),
            "normalizedUrl": str(reference.get("normalizedUrl") or ""),
            "marker": marker,
            "citationScope": scope,
            "sourceLevel": source_level(reference, str(platform.get("platform") or "")),
            "capturedText": preview,
            "capturedTextLength": len(full_text),
            "capturedTextTruncated": preview != full_text,
        }
        for field in (
            "platformTrustSource",
            "contentAcquisition",
            "originAttributionStatus",
            "snippetProvenance",
            "sourceAcquisitionStatus",
            "sourceResolvedUrl",
            "zone",
        ):
            value = str(reference.get(field) or "").strip()
            if value:
                normalized_reference[field] = value
        for field in ("answerContext", "sourceAcquisitionError"):
            value = str(reference.get(field) or "").strip()
            if value:
                normalized_reference[field] = value
        if reference.get("sameMaterialVerified") is True:
            normalized_reference["sameMaterialVerified"] = True
        platform_url = str(
            reference.get("platformUrl")
            or reference.get("platform_url")
            or reference.get("originalUrl")
            or reference.get("original_url")
            or ""
        )
        if platform_url and platform_url != normalized_reference["originalUrl"]:
            normalized_reference["platformUrl"] = platform_url
        official_origin = verified_official_origin(
            reference, str(platform.get("platform") or "")
        )
        if official_origin:
            normalized_reference["verifiedOfficialOriginUrl"] = official_origin
        references.append(normalized_reference)
    global_indexes = [
        reference["index"]
        for reference in references
        if reference["citationScope"] in ("global", "inline_and_global")
    ]
    citation_mode = (
        "mixed"
        if explicit and global_indexes
        else "explicit"
        if explicit
        else "global"
        if global_indexes
        else "source_labels_only"
        if platform.get("sourceMentions")
        else "unmarked"
    )
    return {
        "platform": platform["platform"],
        "label": platform.get("label") or platform["platform"],
        "answerMarkdown": answer,
        "citationMode": citation_mode,
        "explicitCitationReferenceIndexes": explicit,
        "globalReferenceIndexes": global_indexes,
        "references": references,
        "sourceMentions": [
            {
                "label": str(item.get("label") or ""),
                "marker": str(item.get("marker") or ""),
                "occurrenceCount": int(item.get("occurrenceCount") or 1),
            }
            for item in platform.get("sourceMentions") or []
            if str(item.get("label") or "").strip()
        ],
    }


def build_task(question: str, platforms: list[dict]) -> dict:
    return {
        "schemaVersion": "fact-check-x/comparison-task@1",
        "task": "由当前运行载体完成知识点结构化对比",
        "question": question,
        "executionProtocol": {
            "mode": "single_pass",
            "selfContained": True,
            "maxToolCallsAfterStageAcknowledgement": 3,
            "steps": [
                "读取本任务包一次；不得读取技能源码、契约或完整 results.json 探路",
                "直接写入 comparison-analysis.json 一次；不得先打印或转储完整任务包",
                "执行 complete-comparison 一次，由程序使用 results.json 完整正文校验",
            ],
            "capturedTextPolicy": "capturedText 是从完整采集正文中选出的有界原文预览；完整正文仍在 results.json，由 complete-comparison 校验和重建证据",
        },
        "rules": [
            "只使用任务包中的原始回答和已捕获来源，不使用可信搜索、网络搜索或外部模型 API",
            "合并所有平台的原子事实；同一事实的不同值放在同一知识点",
            "每个知识点及每个平台 claim 只能承载一个连续 answerExcerpt 和至少一个单一来源可完整支撑的事实范围；例如“最高50万元奖励”和“最高300万元研发资助”属于不同资助事项，必须拆成两个知识点",
            "role=direct 表示缺少该点就没有直接回答用户问题，其余为 reference",
            "直接答案采用最小充分原则：删除该知识点后仍能完整回答用户明确所问内容，就必须标为 reference；相关、重要或实用不等于直接答案",
            "地域范围必须服从问题：问全国时省市案例属于 reference；问城市时区县、街道或园区细项属于 reference，除非用户明确询问地区差异或逐区口径",
            "主题范围必须服从问题：只问条件、门槛或比例时，奖励、申报流程、咨询电话等相邻信息属于 reference；用户明确同时询问时才可进入 direct",
            "claimType=fact 表示可验证的事实主张，claimType=recommendation 表示纯操作建议",
            "纯操作建议不因缺少逐句脚标而判为引用不忠实；建议中包含的制度事实、条件、数字或时效必须拆成独立 fact 知识点",
            "每个平台逐点填写 covered、claim、citedReferenceIndexes、faithfulness、reason 和 evidence",
            "covered=true 时必须填写 answerExcerpt；它必须是原回答的连续原文子串，并覆盖当前原子主张",
            "answerExcerpt 必须包含 claim 的全部实质要素和量化值；claim 超出 answerExcerpt 是比较分析错误，不得写成平台来源不足",
            "逐句脚标来源只有在脚标实际出现在当前 answerExcerpt 内时才算与该主张局部绑定；不得用答案后段的脚标反向支持前段主张",
            "局部脚标优先：当前 answerExcerpt 已有局部脚标时，只能使用局部绑定来源，不得再用回答后段或回答级官方来源抬高该主张",
            "当前 answerExcerpt 没有局部脚标时，可填写 answerLevelReferenceIndexes，从本次回答明确返回的参考资料中逐主张做语义匹配；每个索引都必须提供 capturedText 原文证据",
            "回答级语义匹配不是整篇来源自动继承：只有证据原文实际支持当前主张才能判 supported，支持其他补充点的官方来源不得抬高核心点",
            "citationMode=mixed 表示平台同时提供逐句脚标与全局来源列表，不能因存在脚标就丢弃全局来源",
            "sourceMentions 只是页面显示但未暴露 URL 的来源标签，不是可回溯参考文献，不能用于来源忠实性证据",
            "深知晓与深知晓（深度溯源）的可信搜索来源统一按官方材料处理，不以 .gov 域名或外链是否返回作为降级条件；但仍必须与当前主张局部绑定或通过允许的回答级语义匹配绑定",
            "snippetProvenance=answer_context 表示该摘录来自平台回答区，只用于现场存证，不得当作链接原文或来源忠实性证据",
            "evidence.excerpt 必须是对应 capturedText 预览中的连续原文；完整正文由程序在 complete-comparison 时校验",
            "comparison.status 只比较平台主张的事实语义；主张语义相同即 consensus，不得因来源忠实性等级不同降为 partial",
            "核心结论、适用对象和关键条件相同，仅有不改变结论的轻微措辞、范围说明或细节差异时使用 mostly_consensus（外显“基本一致”）",
            "存在会改变适用性、风险判断或结论的重要条件缺失或新增时使用 partial；结论互斥时使用 conflict",
            "不以 normalizedUrl 或重新搜索的 URL 替换 originalUrl",
            "trustedAnchor 用于本次回答已经携带且原文支持当前主张的官方材料：深知晓与深知晓（深度溯源）返回的引用、以及其他平台的 gov.cn 引用均可免于重复可信搜索；深度溯源可凭自身回答所附官方材料独立形成锚点；来源官方不等于内容自动支持，仍须逐主张定位原文",
            "必须生成 synthesisDraft：它只综合本阶段知识点，不得冒充权威结论，status 固定为 unverified",
        ],
        "outputShape": {
            "coreQuestion": "核心问题",
            "synthesisDraft": {
                "status": "unverified",
                "answer": "基于各平台知识点合并形成的综合草案；明确保留冲突、缺口和条件",
                "basisKnowledgePointIds": ["K1"],
            },
            "knowledgePoints": [
                {
                    "id": "K1",
                    "description": "一个原子事实",
                    "role": "direct",
                    "claimType": "fact",
                    "core": True,
                    "claims": {"platform-id": {"covered": True, "claim": "...", "answerExcerpt": "包含当前主张及相连脚标的原回答子串", "citedReferenceIndexes": [1], "answerLevelReferenceIndexes": [], "faithfulness": "supported", "reason": "...", "evidence": [{"referenceIndex": 1, "excerpt": "原文"}]}},
                    "comparison": {"status": "consensus", "summary": "精确说明主张属于一致、基本一致、部分一致还是冲突"},
                    "trustedAnchor": {"eligible": True, "platform": "dknowc-chat", "sourcePolicy": "dknow_official_reference", "officialAnswer": "...", "evidence": [{"referenceIndex": 1, "excerpt": "官方原文", "body": "官方材料正文"}]},
                }
            ],
        },
        "platforms": [task_platform(platform) for platform in platforms],
    }


def normalize_evidence(items: object, references: list[dict], allowed_indexes: set[int]) -> tuple[list[dict], bool]:
    evidence = []
    invalid = False
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            invalid = True
            continue
        index = item.get("referenceIndex")
        excerpt = str(item.get("excerpt") or "").strip()
        if not isinstance(index, int) or index not in allowed_indexes or not excerpt:
            invalid = True
            continue
        captured = reference_text(references[index - 1])
        if captured and excerpt in captured:
            evidence.append({"referenceIndex": index, "excerpt": excerpt})
        else:
            invalid = True
    return evidence, invalid


def normalize_claim(raw: object, platform: dict, kid: str, claim_type: str, analysis_gaps: list[dict]) -> dict:
    item = raw if isinstance(raw, dict) else {}
    references = platform.get("references") or []
    answer = str(platform.get("answerMarkdown") or "")
    explicit_indexes = inline_reference_indexes(answer, references)
    global_indexes = {
        index
        for index, ref in enumerate(references, 1)
        if citation_scope(ref, answer) in ("global", "inline_and_global")
    }
    citation_mode = (
        "mixed"
        if explicit_indexes and global_indexes
        else "explicit"
        if explicit_indexes
        else "global"
        if global_indexes
        else "unmarked"
    )
    requested = []
    for index in item.get("citedReferenceIndexes") or []:
        if isinstance(index, int) and 1 <= index <= len(references) and index not in requested:
            requested.append(index)
    answer_level_requested = []
    for index in item.get("answerLevelReferenceIndexes") or []:
        if isinstance(index, int) and 1 <= index <= len(references) and index not in answer_level_requested:
            answer_level_requested.append(index)
    claim_text = str(item.get("claim") or "").strip()
    answer_excerpt = str(item.get("answerExcerpt") or "").strip()
    excerpt_valid = bool(answer_excerpt) and answer_excerpt in answer
    covered = bool(item.get("covered")) and bool(claim_text)
    scope_overreach_reason = ""
    if covered and not excerpt_valid:
        analysis_gaps.append({
            "stage": "comparison",
            "knowledgePointId": kid,
            "platform": platform["platform"],
            "reason": "当前主张缺少可定位的 answerExcerpt，或该片段不是原回答的连续子串",
        })
        answer_excerpt = ""
    elif covered:
        scope_overreach_reason = claim_scope_overreach_reason(claim_text, answer_excerpt)
        if scope_overreach_reason:
            analysis_gaps.append({
                "stage": "comparison",
                "knowledgePointId": kid,
                "platform": platform["platform"],
                "reasonCode": "claim_scope_overreach",
                "responsibility": "comparison_analysis",
                "blocking": True,
                "reason": f"{scope_overreach_reason}；必须补全覆盖全部主张的 answerExcerpt 或拆分知识点后重跑，不能归因于平台缺证",
            })
    declared_indexes = list(dict.fromkeys(requested + answer_level_requested))
    locally_bound_indexes = set()
    if excerpt_valid:
        # Some UIs flatten adjacent markers 1 and 2 into a naked trailing "12".
        # Prefer the carrier's declared split only when it exactly reconstructs
        # that cluster; bracketed 【12】 and a declared [12] remain marker 12.
        locally_bound_indexes = declared_cluster_reference_indexes(
            answer_excerpt,
            references,
            declared_indexes,
        ) or inline_reference_indexes(answer_excerpt, references)
    local_evidence_by_index = {}
    if covered:
        for index, reference in enumerate(references, 1):
            excerpt = locally_supported_claim_evidence(answer_excerpt, reference, claim_text)
            if excerpt:
                locally_bound_indexes.add(index)
                local_evidence_by_index[index] = excerpt
    if locally_bound_indexes:
        candidate_indexes = sorted(locally_bound_indexes)
    else:
        candidate_indexes = declared_indexes
    raw_evidence_by_index = {}
    for evidence_item in item.get("evidence") or []:
        if not isinstance(evidence_item, dict):
            continue
        index = evidence_item.get("referenceIndex")
        excerpt = str(evidence_item.get("excerpt") or "").strip()
        if (
            isinstance(index, int)
            and 1 <= index <= len(references)
            and excerpt
            and excerpt in reference_text(references[index - 1])
        ):
            raw_evidence_by_index[index] = excerpt
    recovered_evidence = []
    supported_indexes = []
    used_partial_local_evidence = False
    if covered:
        for index in candidate_indexes:
            excerpt = supporting_excerpt(references[index - 1], claim_text)
            if not excerpt:
                excerpt = local_evidence_by_index.get(index, "")
                used_partial_local_evidence = used_partial_local_evidence or bool(excerpt)
            if not excerpt:
                raw_excerpt = raw_evidence_by_index.get(index, "")
                if raw_excerpt and raw_evidence_supports_claim(claim_text, raw_excerpt):
                    excerpt = raw_excerpt
            if excerpt:
                supported_indexes.append(index)
                recovered_evidence.append({"referenceIndex": index, "excerpt": excerpt})
        if not recovered_evidence and not locally_bound_indexes:
            for index, reference in enumerate(references, 1):
                if index in candidate_indexes:
                    continue
                excerpt = supporting_excerpt(reference, claim_text)
                if excerpt:
                    supported_indexes.append(index)
                    recovered_evidence.append({"referenceIndex": index, "excerpt": excerpt})
        if used_partial_local_evidence and recovered_evidence and not text_supports_claim(
            claim_text,
            "\n".join(item["excerpt"] for item in recovered_evidence),
        ):
            supported_indexes = []
            recovered_evidence = []
    raw_faithfulness = item.get("faithfulness")
    if raw_faithfulness == "contradicted":
        allowed = set(candidate_indexes)
        evidence, invalid = normalize_evidence(item.get("evidence"), references, allowed)
        faithfulness = "contradicted" if evidence and not invalid else "insufficient"
        effective_indexes = [
            index for index in candidate_indexes
            if any(item["referenceIndex"] == index for item in evidence)
        ]
    else:
        evidence = recovered_evidence
        faithfulness = "supported" if evidence else "insufficient"
        effective_indexes = supported_indexes
    if scope_overreach_reason:
        evidence = []
        faithfulness = "insufficient"
        effective_indexes = []
    if claim_type == "recommendation" and covered and faithfulness == "insufficient":
        faithfulness = "not_applicable"
    if (
        faithfulness == "insufficient"
        and covered
        and claim_type == "fact"
        and not scope_overreach_reason
    ):
        analysis_gaps.append({
            "stage": "comparison",
            "knowledgePointId": kid,
            "platform": platform["platform"],
            "reason": "当前回答所附来源中未定位到支持该主张的原文",
        })
    requested = [
        index for index in effective_indexes
        if index in locally_bound_indexes or index in global_indexes
    ]
    answer_level_requested = [
        index for index in effective_indexes if index not in requested
    ]
    levels = [source_level(references[index - 1], platform["platform"]) for index in effective_indexes]
    level = (
        "official"
        if "official" in levels
        else "dknow_trusted_search_official"
        if "dknow_trusted_search_official" in levels
        else "nonofficial"
        if levels
        else "none"
    )
    binding_mode = (
        "local"
        if any(index in locally_bound_indexes for index in requested)
        else "declared_global"
        if requested
        else "answer_level_semantic"
        if answer_level_requested
        else "none"
    )
    binding_reason = {
        "local": "逐段溯源",
        "declared_global": "回答级来源",
        "answer_level_semantic": "全文语义溯源",
        "none": "未建立溯源",
    }[binding_mode]
    faithfulness_reason = {
        "supported": "来源原文支持当前主张",
        "contradicted": "来源原文与当前主张矛盾",
        "insufficient": "当前来源证据不足",
        "not_applicable": "纯操作建议，直接引用不适用",
    }[faithfulness]
    normalized_reason = (
        f"主张写宽；{scope_overreach_reason}；必须补全 answerExcerpt 或拆分后重跑，不能归因于平台来源不足"
        if covered and scope_overreach_reason
        else
        faithfulness_reason
        if covered and faithfulness == "not_applicable"
        else f"{binding_reason}；{faithfulness_reason}"
        if covered
        else ""
    )
    return {
        "claimType": claim_type,
        "covered": covered,
        "claim": clipped(item.get("claim"), 1000) if covered else "",
        "answerExcerpt": clipped(answer_excerpt, 4000) if covered else "",
        "locallyBoundReferenceIndexes": sorted(locally_bound_indexes),
        "citationMode": citation_mode,
        "citedReferenceIndexes": effective_indexes,
        "answerLevelReferenceIndexes": answer_level_requested,
        "referenceBinding": binding_mode,
        "sourceLevel": level,
        "faithfulness": faithfulness if covered else "insufficient",
        "insufficiencyCause": "claim_scope_overreach" if scope_overreach_reason else "",
        "responsibility": "comparison_analysis" if scope_overreach_reason else "",
        "reason": normalized_reason,
        "evidence": evidence,
    }


def normalize_anchor(raw: object, point_claims: dict, platform_map: dict, kid: str, claim_type: str, analysis_gaps: list[dict]) -> dict:
    if claim_type == "recommendation":
        return {"eligible": False}
    item = raw if isinstance(raw, dict) else {}
    requested_pid = str(item.get("platform") or "")
    candidate_pids = []
    if requested_pid in platform_map:
        candidate_pids.append(requested_pid)
    candidate_pids.extend(
        pid
        for pid in ("dknowc-chat", "dknowc-deep-research")
        if pid in platform_map and pid not in candidate_pids
    )
    candidate_pids.extend(
        pid for pid in platform_map if pid not in candidate_pids
    )
    candidates = []
    for pid in candidate_pids:
        claim = point_claims.get(pid) or {}
        platform = platform_map.get(pid)
        if (
            platform is None
            or not claim.get("covered")
            or claim.get("faithfulness") != "supported"
        ):
            continue
        official_answer = clipped(claim.get("claim"), 1000)
        references = platform.get("references") or []
        evidence = []
        policies = set()
        claim_evidence = {
            entry.get("referenceIndex"): str(entry.get("excerpt") or "").strip()
            for entry in claim.get("evidence") or []
            if isinstance(entry, dict)
            and isinstance(entry.get("referenceIndex"), int)
            and str(entry.get("excerpt") or "").strip()
        }
        for reference_index, reference in enumerate(references, 1):
            policy = official_anchor_policy(reference, pid)
            if not policy:
                continue
            excerpt = claim_evidence.get(reference_index) or supporting_excerpt(
                reference, official_answer
            )
            body = reference_text(reference)
            if not excerpt or not body:
                continue
            policies.add(policy)
            evidence.append({
                "referenceIndex": reference_index,
                "excerpt": excerpt,
                "body": clipped(body, 6000),
            })
        if not evidence or len(policies) != 1:
            continue
        if not official_answer:
            continue
        source_policy = next(iter(policies))
        candidates.append({
            "platform": pid,
            "sourcePolicy": source_policy,
            "officialAnswer": official_answer,
            "references": references,
            "evidence": evidence,
        })
    if candidates:
        policy_counts = {
            policy: sum(candidate["sourcePolicy"] == policy for candidate in candidates)
            for policy in {candidate["sourcePolicy"] for candidate in candidates}
        }
        source_policy = max(
            policy_counts,
            key=lambda policy: (
                policy_counts[policy],
                policy == "dknow_official_reference",
            ),
        )
        # Keep every platform's independently captured official material in one
        # immutable pool.  Choosing a primary policy is only for the legacy
        # searchMode label; it must never discard a gov.cn source just because
        # the same point also has a DeepKnow official source (or vice versa).
        primary = next(
            candidate for candidate in candidates
            if candidate["sourcePolicy"] == source_policy
        )
        anchor_evidence = []
        claim_evidence_map = {}
        seen = set()
        for candidate in candidates:
            ids = []
            references = candidate["references"]
            for evidence_item in candidate["evidence"]:
                reference = references[evidence_item["referenceIndex"] - 1]
                identity = (
                    candidate["platform"],
                    evidence_item["referenceIndex"],
                    str(reference.get("url") or ""),
                    evidence_item["excerpt"],
                )
                if identity in seen:
                    continue
                seen.add(identity)
                evidence_id = f"A{len(anchor_evidence) + 1}"
                ids.append(evidence_id)
                anchor_evidence.append({
                "id": evidence_id,
                "title": str(reference.get("title") or reference.get("url") or ""),
                "url": str(reference.get("url") or ""),
                "excerpt": evidence_item["excerpt"],
                "body": evidence_item["body"],
                "referenceIndex": evidence_item["referenceIndex"],
                "contentAcquisition": str(reference.get("contentAcquisition") or ""),
                "sameMaterialVerified": reference.get("sameMaterialVerified") is True,
                "originAttributionStatus": str(reference.get("originAttributionStatus") or ""),
                "platformUrl": str(
                    reference.get("platformUrl")
                    or reference.get("platform_url")
                    or reference.get("originalUrl")
                    or reference.get("original_url")
                    or ""
                ),
                "zone": str(reference.get("zone") or ""),
                "platformTrustSource": str(
                    reference.get("platformTrustSource")
                    or (
                        "dknow_reference_capture"
                        if source_policy == "dknow_official_reference"
                        else ""
                    )
                ),
                "evidencePlatform": candidate["platform"],
                "sourcePolicy": candidate["sourcePolicy"],
                })
            if ids:
                claim_evidence_map[candidate["platform"]] = ids
        return {
            "eligible": True,
            "platform": primary["platform"],
            "sourcePolicy": source_policy,
            "sourcePolicies": sorted(policy_counts),
            "trustedSearchUsed": source_policy == "dknow_official_reference",
            "officialAnswer": primary["officialAnswer"],
            "evidence": anchor_evidence,
            "claimEvidenceMap": claim_evidence_map,
        }
    if item.get("eligible"):
        analysis_gaps.append({
            "stage": "comparison",
            "knowledgePointId": kid,
            "platform": requested_pid or "dknowc-chat",
            "reason": "本次回答已有官方材料未能定位到支持当前知识点的原文",
        })
    return {"eligible": False}


def normalize_comparison_status(raw_status: object, summary: str, covered_count: int) -> str:
    status = str(raw_status or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "agreement": "consensus",
        "agree": "consensus",
        "agreed": "consensus",
        "consistent": "consensus",
        "same": "consensus",
        "一致": "consensus",
        "完全一致": "consensus",
        "mostly_consensus": "mostly_consensus",
        "mostly_consistent": "mostly_consensus",
        "substantially_consistent": "mostly_consensus",
        "minor_difference": "mostly_consensus",
        "basic_consensus": "mostly_consensus",
        "基本一致": "mostly_consensus",
        "partial_agreement": "partial",
        "partially_consistent": "partial",
        "difference": "partial",
        "disagreement": "conflict",
        "contradiction": "conflict",
        "冲突": "conflict",
    }
    status = aliases.get(status, status)
    if status in ("consensus", "mostly_consensus", "conflict", "partial", "single"):
        return status
    compact_summary = re.sub(r"\s+", "", summary)
    if "基本一致" in compact_summary and not any(term in compact_summary for term in ("部分一致", "不一致", "冲突")):
        return "mostly_consensus"
    if compact_summary and any(term in compact_summary for term in ("完全一致", "数值一致", "均回答", "均确认", "说法一致")):
        if not any(term in compact_summary for term in ("部分一致", "不一致", "冲突")):
            return "consensus"
    return "single" if covered_count <= 1 else "partial"


def validate_analysis_contract(raw: dict, platforms: list[dict]) -> None:
    """Require semantic decisions while leaving mechanical citation repair to code."""
    errors = []
    required_claim_fields = (
        "covered",
        "claim",
        "answerExcerpt",
        "faithfulness",
        "evidence",
    )
    platform_map = {platform["platform"]: platform for platform in platforms}
    platform_ids = list(platform_map)
    points = raw.get("knowledgePoints")
    if not isinstance(points, list) or not points:
        raise SkillError("1.1 分析未产生任何知识点")
    synthesis = raw.get("synthesisDraft")
    allowed_point_ids = {f"K{position}" for position in range(1, len(points) + 1)}
    if not isinstance(synthesis, dict):
        errors.append("synthesisDraft 缺失")
    else:
        if synthesis.get("status") != "unverified":
            errors.append("synthesisDraft.status 必须为 unverified")
        if not str(synthesis.get("answer") or "").strip():
            errors.append("synthesisDraft.answer 不能为空")
        basis_ids = synthesis.get("basisKnowledgePointIds")
        if not isinstance(basis_ids, list) or not basis_ids:
            errors.append("synthesisDraft.basisKnowledgePointIds 必须是非空数组")
        elif (
            any(not isinstance(item, str) or item not in allowed_point_ids for item in basis_ids)
            or len(basis_ids) != len(set(basis_ids))
        ):
            errors.append("synthesisDraft.basisKnowledgePointIds 含未知或重复知识点")
    total_covered = 0
    for position, point in enumerate(points, 1):
        path = f"knowledgePoints[{position - 1}]"
        if not isinstance(point, dict):
            errors.append(f"{path} 必须是对象")
            continue
        claims = point.get("claims")
        claim_type = str(point.get("claimType") or "fact")
        if claim_type not in ("fact", "recommendation"):
            errors.append(f"{path}.claimType 必须是 fact 或 recommendation")
        if not isinstance(claims, dict):
            errors.append(f"{path}.claims 缺失")
            continue
        for platform_id in platform_ids:
            claim = claims.get(platform_id)
            claim_path = f"{path}.claims.{platform_id}"
            if not isinstance(claim, dict):
                errors.append(f"{claim_path} 缺失")
                continue
            missing_fields = [
                field for field in required_claim_fields if field not in claim
            ]
            if missing_fields:
                errors.append(
                    f"{claim_path} 缺少必填字段：{','.join(missing_fields)}"
                )
            if not isinstance(claim.get("covered"), bool):
                errors.append(f"{claim_path}.covered 必须为布尔值")
            faithfulness = claim.get("faithfulness")
            if faithfulness not in ("supported", "contradicted", "insufficient", "not_applicable"):
                errors.append(f"{claim_path}.faithfulness 非法")
            if (
                faithfulness == "not_applicable"
                and claim_type != "recommendation"
                and claim.get("covered") is True
            ):
                errors.append(f"{claim_path}.faithfulness 只能在操作建议中为 not_applicable")
            evidence = claim.get("evidence")
            if not isinstance(evidence, list):
                errors.append(f"{claim_path}.evidence 必须是数组")
                evidence = []
            answer = str(platform_map[platform_id].get("answerMarkdown") or "")
            claim_text = str(claim.get("claim") or "").strip()
            answer_excerpt = str(claim.get("answerExcerpt") or "").strip()
            if claim.get("covered") is True:
                total_covered += 1
                if not claim_text:
                    errors.append(f"{claim_path}.claim 不能为空")
                if not answer_excerpt or answer_excerpt not in answer:
                    errors.append(f"{claim_path}.answerExcerpt 必须是原回答的连续非空子串")
                references = platform_map[platform_id].get("references") or []
                requested = claim.get("citedReferenceIndexes") or []
                answer_level = claim.get("answerLevelReferenceIndexes") or []
                if not isinstance(requested, list) or not isinstance(answer_level, list):
                    errors.append(f"{claim_path} 引用索引必须是数组")
                    requested, answer_level = [], []
                for item in evidence:
                    if not isinstance(item, dict):
                        errors.append(f"{claim_path}.evidence 项必须是对象")
                        continue
        comparison = point.get("comparison")
        if "comparison" not in point:
            errors.append(f"{path}.comparison 缺失")
        elif (
            not isinstance(comparison, dict)
            or (
                comparison.get("status") not in {
                "consensus", "mostly_consensus", "partial", "conflict", "single",
                "agreement", "基本一致",
                }
                or not str(comparison.get("summary") or "").strip()
            )
        ):
            errors.append(f"{path}.comparison.status/summary 非法")
        anchor = point.get("trustedAnchor")
        if "trustedAnchor" not in point:
            errors.append(f"{path}.trustedAnchor 缺失")
        elif not isinstance(anchor, dict):
            errors.append(f"{path}.trustedAnchor 必须是对象")
        elif isinstance(anchor, dict) and anchor.get("eligible"):
            if (
                anchor.get("platform") not in platform_map
                or not str(anchor.get("officialAnswer") or "").strip()
                or not isinstance(anchor.get("evidence"), list)
                or not anchor.get("evidence")
            ):
                errors.append(f"{path}.trustedAnchor 合格声明字段不完整")
    if total_covered == 0:
        errors.append("knowledgePoints 所有平台均为 covered=false；至少一个真实主张必须被覆盖")
    if errors:
        preview = "；".join(errors[:12])
        suffix = f"；另有 {len(errors) - 12} 项" if len(errors) > 12 else ""
        raise SkillError(
            "1.1 载体输出不满足 fact-check-x/product-truth@1，"
            f"必须在本阶段修复或重试，禁止进入权威核验：{preview}{suffix}"
        )


def normalize(raw: dict, source: dict, question: str, platforms: list[dict]) -> dict:
    validate_analysis_contract(raw, platforms)
    platform_map = {platform["platform"]: platform for platform in platforms}
    analysis_gaps = []
    points = []
    for position, raw_point in enumerate(raw.get("knowledgePoints") or [], 1):
        if not isinstance(raw_point, dict):
            raise SkillError(f"第 {position} 个知识点必须是对象")
        kid = f"K{position}"
        description = clipped(raw_point.get("description"), 500)
        if not description:
            raise SkillError(f"{kid} 缺少知识点描述")
        role, role_reason = normalize_role(question, description, raw_point.get("role"))
        claim_type = normalize_claim_type(
            description,
            raw_point.get("claimType"),
            raw_point.get("claims"),
        )
        claims = {
            pid: normalize_claim(
                (raw_point.get("claims") or {}).get(pid),
                platform,
                kid,
                claim_type,
                analysis_gaps,
            )
            for pid, platform in platform_map.items()
        }
        comparison_raw = raw_point.get("comparison") if isinstance(raw_point.get("comparison"), dict) else {}
        covered_count = sum(claim["covered"] for claim in claims.values())
        comparison_summary = clipped(comparison_raw.get("summary"), 500)
        if not comparison_summary:
            comparison_summary = (
                "多个平台均覆盖该知识点，具体表述见逐平台主张。"
                if covered_count > 1
                else "该平台覆盖该知识点。"
            )
        status = normalize_comparison_status(
            comparison_raw.get("status"),
            comparison_summary,
            covered_count,
        )
        anchor = normalize_anchor(raw_point.get("trustedAnchor"), claims, platform_map, kid, claim_type, analysis_gaps)
        points.append({
            "id": kid,
            "description": description,
            "role": role,
            "roleNormalizationReason": role_reason,
            "claimType": claim_type,
            "core": bool(raw_point.get("core")) and role == "direct",
            "claims": claims,
            "comparison": {"status": status, "summary": comparison_summary},
            "trustedAnchor": anchor,
        })
    if not points:
        raise SkillError("1.1 分析未产生任何知识点")
    synthesis_raw = raw["synthesisDraft"]
    return {
        "schemaVersion": "fact-check-x/comparison@1",
        "question": question,
        "coreQuestion": clipped(raw.get("coreQuestion") or question, 500),
        "synthesisDraft": {
            "status": "unverified",
            "answer": clipped(synthesis_raw.get("answer"), 6000),
            "basisKnowledgePointIds": list(
                synthesis_raw.get("basisKnowledgePointIds") or []
            ),
        },
        "createdAt": now_iso(),
        "sourceSchemaVersion": source.get("schemaVersion"),
        "platforms": [{"platform": p["platform"], "label": p.get("label") or p["platform"]} for p in platforms],
        "knowledgePoints": points,
        "analysisGaps": analysis_gaps,
    }


def canonical_analysis(comparison: dict) -> dict:
    return {
        "schemaVersion": "fact-check-x/comparison-analysis@1",
        "coreQuestion": comparison.get("coreQuestion"),
        "synthesisDraft": comparison.get("synthesisDraft") or {},
        "knowledgePoints": [
            {
                "id": point.get("id"),
                "description": point.get("description"),
                "role": point.get("role"),
                "claimType": point.get("claimType", "fact"),
                "core": point.get("core"),
                "claims": point.get("claims") or {},
                "comparison": point.get("comparison") or {},
                "trustedAnchor": point.get("trustedAnchor") or {"eligible": False},
            }
            for point in comparison.get("knowledgePoints") or []
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成或验收知识点结构化对比。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--task-output")
    parser.add_argument("--analysis")
    parser.add_argument("--output")
    parser.add_argument("--canonical-analysis-output")
    args = parser.parse_args()
    try:
        source = load_json(args.input)
        question, platforms = validate_results(source)
        if args.task_output:
            dump_json(args.task_output, build_task(question, platforms))
        if args.analysis:
            if not args.output:
                raise SkillError("使用 --analysis 时必须提供 --output")
            raw = load_json(args.analysis)
            if not isinstance(raw, dict):
                raise SkillError("comparison-analysis.json 必须是对象")
            result = normalize(raw, source, question, platforms)
            if args.canonical_analysis_output:
                dump_json(args.canonical_analysis_output, canonical_analysis(result))
            dump_json(args.output, result)
            print(json.dumps({"status": "completed", "output": str(Path(args.output).resolve()), "knowledgePoints": len(result["knowledgePoints"]), "analysisGapCount": len(result["analysisGaps"])}, ensure_ascii=False))
            return 0
        if args.task_output:
            print(json.dumps({"status": "prepared", "task": str(Path(args.task_output).resolve())}, ensure_ascii=False))
            return 0
        raise SkillError("至少提供 --task-output 或 --analysis")
    except (SkillError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
