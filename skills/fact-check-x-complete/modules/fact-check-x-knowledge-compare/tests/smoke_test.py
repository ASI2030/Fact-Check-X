#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def run(arguments: list[str]) -> None:
    proc = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if proc.returncode:
        raise AssertionError(proc.stdout or proc.stderr)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fact-check-x-11-") as temp:
        out = Path(temp)
        task = out / "task.json"
        comparison = out / "comparison.json"
        report = out / "comparison.html"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(FIXTURES / "results.json"), "--task-output", str(task)])
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(FIXTURES / "results.json"), "--analysis", str(FIXTURES / "comparison-analysis.json"), "--output", str(comparison)])
        run([sys.executable, str(ROOT / "scripts" / "render_comparison.py"), "--results", str(FIXTURES / "results.json"), "--comparison", str(comparison), "--output", str(report)])
        task_data = json.loads(task.read_text(encoding="utf-8"))
        result = json.loads(comparison.read_text(encoding="utf-8"))
        html = report.read_text(encoding="utf-8")
        assert task_data["platforms"][0]["citationMode"] == "explicit"
        assert task_data["platforms"][1]["citationMode"] == "global"
        point = result["knowledgePoints"][0]
        assert result["synthesisDraft"]["status"] == "unverified"
        assert result["synthesisDraft"]["basisKnowledgePointIds"] == ["K1"]
        assert point["comparison"]["status"] == "conflict"
        assert point["claims"]["dknowc-chat"]["citedReferenceIndexes"] == [1]
        assert point["claims"]["doubao"]["sourceLevel"] == "nonofficial"
        assert point["trustedAnchor"]["eligible"] is True
        assert result["analysisGaps"] == []
        assert "platform-grid" in html and "逐知识点完整对照" in html
        assert "综合草案" in html and "尚未经过权威核验" in html
        assert "官方来源" in html
        assert "可信搜索官方原站" not in html
        assert "非官方来源" in html
        assert "role-direct" in html and "role-reference" in html
        assert "role-badge" in html
        assert "trusted_repository" not in html
        assert 'href="01-capture-report.html"' in html
        assert 'href="03-authority-report.html"' in html
        assert 'href="04-final-report.html"' in html
        for current_term in ("依据展示", "溯源方式", "直接展示", "逐段溯源", "无对应的清单"):
            assert current_term in html
        for retired_term in ("页面模式", "绑定方式", "显式标记", "局部角标绑定", "平台声明全局来源"):
            assert retired_term not in html

        official_results = {
            "schemaVersion": "1",
            "question": "广州无合同租房提取住房公积金每月最高多少？",
            "platforms": [{
                "platform": "deepseek",
                "label": "DeepSeek",
                "status": "success",
                "answerMarkdown": "每人每月最高提取额度为1400元【1】。",
                "references": [{
                    "title": "广州住房公积金规则",
                    "url": "https://gjj.gz.gov.cn/example",
                    "marker": "1",
                    "snippet": "每人每月最高提取额度为1400元。",
                }],
            }],
        }
        official_analysis = {
            "schemaVersion": "fact-check-x/comparison-analysis@1",
            "coreQuestion": official_results["question"],
            "synthesisDraft": {
                "status": "unverified",
                "answer": "待核验",
                "basisKnowledgePointIds": ["K1"],
            },
            "knowledgePoints": [{
                "id": "K1",
                "description": "无合同租房月度提取上限",
                "role": "direct",
                "core": True,
                "claims": {"deepseek": {
                    "covered": True,
                    "claim": "每人每月最高提取额度为1400元",
                    "answerExcerpt": "每人每月最高提取额度为1400元【1】。",
                    "citedReferenceIndexes": [1],
                    "answerLevelReferenceIndexes": [],
                    "faithfulness": "supported",
                    "reason": "",
                    "evidence": [{
                        "referenceIndex": 1,
                        "excerpt": "每人每月最高提取额度为1400元。",
                    }],
                }},
                "comparison": {"status": "single", "summary": "单平台回答"},
                "trustedAnchor": {"eligible": False},
            }],
        }
        official_results_path = out / "gov-results.json"
        official_analysis_path = out / "gov-analysis.json"
        official_output_path = out / "gov-comparison.json"
        official_results_path.write_text(
            json.dumps(official_results, ensure_ascii=False), encoding="utf-8"
        )
        official_analysis_path.write_text(
            json.dumps(official_analysis, ensure_ascii=False), encoding="utf-8"
        )
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(official_results_path),
            "--analysis",
            str(official_analysis_path),
            "--output",
            str(official_output_path),
        ])
        gov_anchor = json.loads(
            official_output_path.read_text(encoding="utf-8")
        )["knowledgePoints"][0]["trustedAnchor"]
        assert gov_anchor["eligible"] is True
        assert gov_anchor["sourcePolicy"] == "gov_cn_reference"
        assert gov_anchor["platform"] == "deepseek"

        nonofficial_results = json.loads(json.dumps(official_results, ensure_ascii=False))
        nonofficial_results["platforms"][0]["references"][0]["url"] = (
            "https://example.com/policy"
        )
        nonofficial_results_path = out / "nonofficial-results.json"
        nonofficial_output_path = out / "nonofficial-comparison.json"
        nonofficial_results_path.write_text(
            json.dumps(nonofficial_results, ensure_ascii=False), encoding="utf-8"
        )
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(nonofficial_results_path),
            "--analysis",
            str(official_analysis_path),
            "--output",
            str(nonofficial_output_path),
        ])
        assert json.loads(
            nonofficial_output_path.read_text(encoding="utf-8")
        )["knowledgePoints"][0]["trustedAnchor"] == {"eligible": False}

        unsupported_results = json.loads(json.dumps(official_results, ensure_ascii=False))
        unsupported_results["platforms"][0]["references"][0]["snippet"] = (
            "本材料只说明线上办理流程，不包含提取额度。"
        )
        unsupported_results_path = out / "unsupported-gov-results.json"
        unsupported_output_path = out / "unsupported-gov-comparison.json"
        unsupported_results_path.write_text(
            json.dumps(unsupported_results, ensure_ascii=False), encoding="utf-8"
        )
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(unsupported_results_path),
            "--analysis",
            str(official_analysis_path),
            "--output",
            str(unsupported_output_path),
        ])
        assert json.loads(
            unsupported_output_path.read_text(encoding="utf-8")
        )["knowledgePoints"][0]["trustedAnchor"] == {"eligible": False}

        omitted_boolean = json.loads(
            (FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8")
        )
        for platform_claim in omitted_boolean["knowledgePoints"][0]["claims"].values():
            platform_claim.pop("covered", None)
            platform_claim.pop("faithfulness", None)
            platform_claim.pop("reason", None)
        omitted_boolean_path = out / "omitted-boolean-analysis.json"
        omitted_boolean_path.write_text(
            json.dumps(omitted_boolean, ensure_ascii=False),
            encoding="utf-8",
        )
        omitted_boolean_result = out / "omitted-boolean-comparison.json"
        rejected_missing_fields = subprocess.run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(FIXTURES / "results.json"),
            "--analysis",
            str(omitted_boolean_path),
            "--output",
            str(omitted_boolean_result),
        ], text=True, capture_output=True, check=False)
        assert rejected_missing_fields.returncode != 0
        assert "product-truth@1" in rejected_missing_fields.stdout
        assert "缺少必填字段" in rejected_missing_fields.stdout
        assert not omitted_boolean_result.exists()

        three_results = json.loads((FIXTURES / "results.json").read_text(encoding="utf-8"))
        deepseek = json.loads(json.dumps(three_results["platforms"][1], ensure_ascii=False))
        deepseek.update({
            "platform": "deepseek",
            "label": "DeepSeek",
            "url": "https://chat.deepseek.com/",
            "answerMarkdown": "每人每月最高提取 2000 元。",
        })
        three_results["platforms"].append(deepseek)
        three_comparison = json.loads(comparison.read_text(encoding="utf-8"))
        three_comparison["platforms"].append({"platform": "deepseek", "label": "DeepSeek"})
        three_comparison["knowledgePoints"][0]["claims"]["deepseek"] = json.loads(
            json.dumps(three_comparison["knowledgePoints"][0]["claims"]["doubao"], ensure_ascii=False)
        )
        three_results_path = out / "three-results.json"
        three_comparison_path = out / "three-comparison.json"
        three_report = out / "three-comparison.html"
        three_results_path.write_text(json.dumps(three_results, ensure_ascii=False), encoding="utf-8")
        three_comparison_path.write_text(json.dumps(three_comparison, ensure_ascii=False), encoding="utf-8")
        run([
            sys.executable,
            str(ROOT / "scripts" / "render_comparison.py"),
            "--results",
            str(three_results_path),
            "--comparison",
            str(three_comparison_path),
            "--output",
            str(three_report),
        ])
        three_html = three_report.read_text(encoding="utf-8")
        assert three_html.count('class="answer-panel"') == 3
        assert "DeepSeek" in three_html
        assert 'multi-platform platform-layout-compact platform-count-3' in three_html
        assert 'style="--platform-count:3"' in three_html
        assert "grid-template-columns:repeat(var(--platform-count),minmax(0,1fr))" in three_html
        assert "min-width:1160px" in three_html
        assert "grid-template-columns:repeat(auto-fit,minmax(320px,1fr))" not in three_html

        invalid = json.loads((FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8"))
        invalid_claim = invalid["knowledgePoints"][0]["claims"]["dknowc-chat"]
        invalid_claim["citedReferenceIndexes"].append(2)
        invalid_claim["evidence"].append({"referenceIndex": 2, "excerpt": "这条来源不应被显式引用模式使用"})
        invalid_path = out / "invalid-analysis.json"
        invalid_path.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
        invalid_result = out / "invalid-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(FIXTURES / "results.json"), "--analysis", str(invalid_path), "--output", str(invalid_result)])
        rejected = json.loads(invalid_result.read_text(encoding="utf-8"))
        assert rejected["knowledgePoints"][0]["claims"]["dknowc-chat"]["citedReferenceIndexes"] == [1]
        assert rejected["analysisGaps"] == []

        mixed_source = json.loads((FIXTURES / "results.json").read_text(encoding="utf-8"))
        mixed_source["platforms"][1]["answerMarkdown"] = "每人每月最高提取 2000 元。[1] 2024年11月起执行。"
        mixed_source["platforms"][1]["references"][0]["marker"] = "1"
        mixed_source["platforms"][1]["references"][0]["citationScope"] = "inline"
        mixed_source["platforms"][1]["references"].append(
            {
                "title": "全局检索政策来源",
                "url": "https://example.com/global-policy",
                "normalizedUrl": "https://example.com/global-policy",
                "snippet": "2024年11月起执行每人每月2000元。",
                "citationScope": "global",
            }
        )
        mixed_source_path = out / "mixed-results.json"
        mixed_source_path.write_text(json.dumps(mixed_source, ensure_ascii=False), encoding="utf-8")
        mixed_task = out / "mixed-task.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(mixed_source_path), "--task-output", str(mixed_task)])
        mixed_task_data = json.loads(mixed_task.read_text(encoding="utf-8"))
        assert mixed_task_data["platforms"][1]["citationMode"] == "mixed"
        assert mixed_task_data["platforms"][1]["globalReferenceIndexes"] == [2]

        agreement = json.loads((FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8"))
        agreement["knowledgePoints"][0]["comparison"] = {
            "status": "agreement",
            "summary": "两平台数值完全一致",
        }
        agreement["knowledgePoints"][0]["claims"]["doubao"]["citedReferenceIndexes"] = [2]
        agreement["knowledgePoints"][0]["claims"]["doubao"]["answerExcerpt"] = "每人每月最高提取 2000 元。[1]"
        agreement["knowledgePoints"][0]["claims"]["doubao"]["faithfulness"] = "supported"
        agreement["knowledgePoints"][0]["claims"]["doubao"]["evidence"] = [
            {"referenceIndex": 2, "excerpt": "2024年11月起执行每人每月2000元。"}
        ]
        agreement_path = out / "agreement-analysis.json"
        agreement_path.write_text(json.dumps(agreement, ensure_ascii=False), encoding="utf-8")
        agreement_result = out / "agreement-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(mixed_source_path), "--analysis", str(agreement_path), "--output", str(agreement_result)])
        agreement_data = json.loads(agreement_result.read_text(encoding="utf-8"))
        assert agreement_data["knowledgePoints"][0]["comparison"]["status"] == "consensus"
        assert agreement_data["knowledgePoints"][0]["claims"]["doubao"]["citedReferenceIndexes"] == []
        assert agreement_data["knowledgePoints"][0]["claims"]["doubao"]["faithfulness"] == "insufficient"

        mostly = json.loads(json.dumps(agreement, ensure_ascii=False))
        mostly["knowledgePoints"][0]["comparison"] = {
            "status": "基本一致",
            "summary": "核心结论和适用对象相同，仅有不改变结论的轻微表述差异",
        }
        mostly_path = out / "mostly-analysis.json"
        mostly_path.write_text(json.dumps(mostly, ensure_ascii=False), encoding="utf-8")
        mostly_result = out / "mostly-comparison.json"
        mostly_report = out / "mostly-comparison.html"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(mixed_source_path), "--analysis", str(mostly_path), "--output", str(mostly_result)])
        run([sys.executable, str(ROOT / "scripts" / "render_comparison.py"), "--results", str(mixed_source_path), "--comparison", str(mostly_result), "--output", str(mostly_report)])
        mostly_data = json.loads(mostly_result.read_text(encoding="utf-8"))
        assert mostly_data["knowledgePoints"][0]["comparison"]["status"] == "mostly_consensus"
        assert "基本一致" in mostly_report.read_text(encoding="utf-8")

        boundary_source = json.loads((FIXTURES / "results.json").read_text(encoding="utf-8"))
        boundary_source["platforms"][1]["answerMarkdown"] = (
            "每人每月最高提取2000元。【1】\n"
            "补充：申请人需连续缴存满3个月。【2】"
        )
        boundary_source["platforms"][1]["references"] = [
            {
                "title": "商业资讯转载",
                "url": "https://example.com/rent",
                "marker": "1",
                "snippet": "每人每月最高提取2000元。",
                "citationScope": "inline",
            },
            {
                "title": "北京住房公积金官方办理指南",
                "url": "https://gjj.beijing.gov.cn/guide",
                "marker": "2",
                "snippet": "申请人需连续足额缴存住房公积金3个月（含）以上。",
                "citationScope": "inline",
            },
        ]
        boundary_path = out / "boundary-results.json"
        boundary_path.write_text(json.dumps(boundary_source, ensure_ascii=False), encoding="utf-8")
        boundary_analysis = json.loads((FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8"))
        claim = boundary_analysis["knowledgePoints"][0]["claims"]["doubao"]
        claim["answerExcerpt"] = "每人每月最高提取2000元。【1】"
        claim["citedReferenceIndexes"] = [1, 2]
        claim["answerLevelReferenceIndexes"] = [2]
        claim["evidence"] = [
            {"referenceIndex": 1, "excerpt": "每人每月最高提取2000元。"},
            {"referenceIndex": 2, "excerpt": "申请人需连续足额缴存住房公积金3个月（含）以上。"},
        ]
        boundary_analysis_path = out / "boundary-analysis.json"
        boundary_analysis_path.write_text(json.dumps(boundary_analysis, ensure_ascii=False), encoding="utf-8")
        boundary_result = out / "boundary-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(boundary_path), "--analysis", str(boundary_analysis_path), "--output", str(boundary_result)])
        boundary_data = json.loads(boundary_result.read_text(encoding="utf-8"))
        boundary_claim = boundary_data["knowledgePoints"][0]["claims"]["doubao"]
        assert boundary_claim["locallyBoundReferenceIndexes"] == [1]
        assert boundary_claim["citedReferenceIndexes"] == [1]
        assert boundary_claim["sourceLevel"] == "nonofficial"
        assert boundary_claim["referenceBinding"] == "local"
        assert boundary_data["analysisGaps"] == []

        answer_level_source = json.loads((FIXTURES / "results.json").read_text(encoding="utf-8"))
        answer_level_source["platforms"][1]["answerMarkdown"] = (
            "每人每月最高提取2000元。\n"
            "补充：申请人需连续缴存满3个月。【2】"
        )
        answer_level_source["platforms"][1]["references"] = [
            {
                "title": "商业资讯转载",
                "url": "https://example.com/rent",
                "marker": "1",
                "snippet": "每人每月最高提取2000元。",
                "citationScope": "inline",
            },
            {
                "title": "北京住房公积金官方办理指南",
                "url": "https://gjj.beijing.gov.cn/guide",
                "marker": "2",
                "snippet": "无发票租房每人每月提取额度为2000元；申请人需连续缴存满3个月。",
                "citationScope": "inline",
            },
        ]
        answer_level_source_path = out / "answer-level-results.json"
        answer_level_source_path.write_text(json.dumps(answer_level_source, ensure_ascii=False), encoding="utf-8")
        answer_level_analysis = json.loads((FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8"))
        answer_level_claim = answer_level_analysis["knowledgePoints"][0]["claims"]["doubao"]
        answer_level_claim["answerExcerpt"] = "每人每月最高提取2000元。"
        answer_level_claim["citedReferenceIndexes"] = []
        answer_level_claim["answerLevelReferenceIndexes"] = [2]
        answer_level_claim["faithfulness"] = "supported"
        answer_level_claim["evidence"] = [
            {"referenceIndex": 2, "excerpt": "无发票租房每人每月提取额度为2000元"}
        ]
        answer_level_analysis_path = out / "answer-level-analysis.json"
        answer_level_analysis_path.write_text(json.dumps(answer_level_analysis, ensure_ascii=False), encoding="utf-8")
        answer_level_result = out / "answer-level-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(answer_level_source_path), "--analysis", str(answer_level_analysis_path), "--output", str(answer_level_result)])
        answer_level_data = json.loads(answer_level_result.read_text(encoding="utf-8"))
        semantic_claim = answer_level_data["knowledgePoints"][0]["claims"]["doubao"]
        assert semantic_claim["referenceBinding"] == "answer_level_semantic"
        assert semantic_claim["sourceLevel"] == "official"
        assert semantic_claim["citedReferenceIndexes"] == [2]
        assert semantic_claim["faithfulness"] == "supported"
        assert semantic_claim["reason"] == "全文语义溯源；来源原文支持当前主张"

        no_backfill_source = json.loads(
            (FIXTURES / "results.json").read_text(encoding="utf-8")
        )
        no_backfill_source["platforms"][1]["answerMarkdown"] = (
            "2.深圳夫妻投靠目前仍执行双2年标准。\n"
            "补充：征求意见稿曾拟将夫妻投靠由2年调整为5年。【2】"
        )
        no_backfill_source["platforms"][1]["references"] = [
            {
                "title": "深圳公安现行办理条件",
                "url": "https://ga.sz.gov.cn/current",
                "marker": "1",
                "content": "被投靠人登记为本市常住户口时间及夫妻结婚登记时间均满2年。",
                "citationScope": "inline",
            },
            {
                "title": "户籍迁入规定征求意见稿",
                "url": "https://example.com/draft",
                "marker": "2",
                "content": "征求意见稿拟将夫妻投靠由2年调整为5年。",
                "citationScope": "inline",
            },
        ]
        no_backfill_source_path = out / "no-backfill-results.json"
        no_backfill_source_path.write_text(
            json.dumps(no_backfill_source, ensure_ascii=False), encoding="utf-8"
        )
        no_backfill_analysis = json.loads(
            (FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8")
        )
        no_backfill_claim = no_backfill_analysis["knowledgePoints"][0]["claims"]["doubao"]
        no_backfill_claim["claim"] = "深圳夫妻投靠目前仍执行双2年标准"
        no_backfill_claim["answerExcerpt"] = "2.深圳夫妻投靠目前仍执行双2年标准。"
        no_backfill_claim["citedReferenceIndexes"] = []
        no_backfill_claim["answerLevelReferenceIndexes"] = [1]
        no_backfill_claim["evidence"] = [
            {
                "referenceIndex": 1,
                "excerpt": "被投靠人登记为本市常住户口时间及夫妻结婚登记时间均满2年",
            }
        ]
        no_backfill_analysis_path = out / "no-backfill-analysis.json"
        no_backfill_analysis_path.write_text(
            json.dumps(no_backfill_analysis, ensure_ascii=False), encoding="utf-8"
        )
        no_backfill_result = out / "no-backfill-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(no_backfill_source_path),
            "--analysis",
            str(no_backfill_analysis_path),
            "--output",
            str(no_backfill_result),
        ])
        no_backfill_data = json.loads(no_backfill_result.read_text(encoding="utf-8"))
        bounded_claim = no_backfill_data["knowledgePoints"][0]["claims"]["doubao"]
        assert bounded_claim["locallyBoundReferenceIndexes"] == []
        assert bounded_claim["citedReferenceIndexes"] == [1]
        assert bounded_claim["answerLevelReferenceIndexes"] == [1]

        answer_context_source = json.loads(
            answer_level_source_path.read_text(encoding="utf-8")
        )
        for reference in answer_context_source["platforms"][1]["references"]:
            reference["snippetProvenance"] = "answer_context"
        answer_context_path = out / "answer-context-results.json"
        answer_context_path.write_text(
            json.dumps(answer_context_source, ensure_ascii=False), encoding="utf-8"
        )
        answer_context_result = out / "answer-context-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(answer_context_path),
            "--analysis",
            str(answer_level_analysis_path),
            "--output",
            str(answer_context_result),
        ])
        answer_context_data = json.loads(
            answer_context_result.read_text(encoding="utf-8")
        )
        answer_context_claim = answer_context_data["knowledgePoints"][0]["claims"][
            "doubao"
        ]
        assert answer_context_claim["faithfulness"] == "insufficient"
        assert answer_context_claim["sourceLevel"] == "none"
        assert answer_context_claim["evidence"] == []
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(answer_context_path),
            "--task-output",
            str(out / "answer-context-task.json"),
        ])
        answer_context_task = json.loads(
            (out / "answer-context-task.json").read_text(encoding="utf-8")
        )
        assert answer_context_task["platforms"][1]["references"][1][
            "snippetProvenance"
        ] == "answer_context"
        assert answer_context_task["platforms"][1]["references"][1][
            "capturedText"
        ] == ""

        deep_research_results = json.loads(
            (FIXTURES / "results.json").read_text(encoding="utf-8")
        )
        deep_research_results["platforms"][0]["platform"] = "dknowc-deep-research"
        deep_research_results["platforms"][0]["label"] = "深知晓（深度溯源）"
        deep_research_task_path = out / "deep-research-task.json"
        deep_research_results_path = out / "deep-research-results.json"
        deep_research_results_path.write_text(
            json.dumps(deep_research_results, ensure_ascii=False), encoding="utf-8"
        )
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(deep_research_results_path),
            "--task-output",
            str(deep_research_task_path),
        ])
        deep_research_task = json.loads(
            deep_research_task_path.read_text(encoding="utf-8")
        )
        assert deep_research_task["platforms"][0]["references"][0][
            "sourceLevel"
        ] == "dknow_trusted_search_official"
        assert "可凭自身回答所附官方材料独立形成锚点" in "\n".join(
            deep_research_task["rules"]
        )
        deep_research_analysis = json.loads(
            (FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8")
        )
        deep_point = deep_research_analysis["knowledgePoints"][0]
        deep_point["claims"]["dknowc-deep-research"] = deep_point["claims"].pop(
            "dknowc-chat"
        )
        deep_point["trustedAnchor"]["platform"] = "dknowc-deep-research"
        deep_research_analysis_path = out / "deep-research-analysis.json"
        deep_research_analysis_path.write_text(
            json.dumps(deep_research_analysis, ensure_ascii=False), encoding="utf-8"
        )
        deep_research_output = out / "deep-research-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(deep_research_results_path),
            "--analysis",
            str(deep_research_analysis_path),
            "--output",
            str(deep_research_output),
        ])
        deep_anchor = json.loads(
            deep_research_output.read_text(encoding="utf-8")
        )["knowledgePoints"][0]["trustedAnchor"]
        assert deep_anchor["eligible"] is True
        assert deep_anchor["platform"] == "dknowc-deep-research"

        compound_results = json.loads(json.dumps(deep_research_results, ensure_ascii=False))
        compound_results["question"] = "在深圳外地人如何买房"
        compound_platform = compound_results["platforms"][0]
        compound_platform["answerMarkdown"] = (
            "单独申请：最高70万元【6】\n"
            "共同申请：最高130万元【6】\n"
            "购买首套住房：上浠60%【6】\n"
            "家庭最高额度可达351万元【5】"
        )
        compound_platform["references"] = [
            {
                "title": "深圳公积金贷款基础额度政策",
                "url": "https://www.sz.gov.cn/policy/base",
                "marker": "6",
                "snippet": "单独申请最高70万元，共同申请最高130万元，购买首套住房上浠60%。",
            },
            {
                "title": "深圳公积金贷款最高额度政策",
                "url": "https://www.sz.gov.cn/policy/maximum",
                "marker": "5",
                "snippet": "同时符合多种上浮条件时，家庭公积金贷款最高额度可达351万元。",
            },
        ]
        compound_results["platforms"] = [compound_platform]
        compound_results_path = out / "compound-deep-results.json"
        compound_results_path.write_text(
            json.dumps(compound_results, ensure_ascii=False), encoding="utf-8"
        )
        compound_analysis = {
            "schemaVersion": "fact-check-x/comparison-analysis@1",
            "coreQuestion": compound_results["question"],
            "knowledgePoints": [{
                "id": "K1",
                "description": "公积金贷款额度及上浮政策",
                "role": "direct",
                "core": True,
                "claims": {
                    "dknowc-deep-research": {
                        "covered": True,
                        "claim": "公积金贷款单独申请最高70万元、共同申请最高130万元，首套上浠60%，家庭最高可达351万元",
                        "answerExcerpt": "单独申请：最高70万元【6】\n共同申请：最高130万元【6】\n购买首套住房：上浠60%【6】\n家庭最高额度可达351万元【5】",
                        "citedReferenceIndexes": [],
                        "answerLevelReferenceIndexes": [],
                        "faithfulness": "insufficient",
                        "reason": "",
                        "evidence": [],
                    }
                },
                "comparison": {"status": "single", "summary": "单平台回答"},
                "trustedAnchor": {"eligible": False},
            }],
            "synthesisDraft": {
                "status": "unverified",
                "warning": "尚未经过权威核验",
                "answer": "待核验",
                "basisKnowledgePointIds": ["K1"],
            },
        }
        compound_analysis_path = out / "compound-deep-analysis.json"
        compound_analysis_path.write_text(
            json.dumps(compound_analysis, ensure_ascii=False), encoding="utf-8"
        )
        compound_output = out / "compound-deep-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(compound_results_path),
            "--analysis",
            str(compound_analysis_path),
            "--output",
            str(compound_output),
        ])
        compound_point = json.loads(compound_output.read_text(encoding="utf-8"))[
            "knowledgePoints"
        ][0]
        compound_claim = compound_point["claims"]["dknowc-deep-research"]
        assert compound_claim["locallyBoundReferenceIndexes"] == [1, 2]
        assert compound_claim["citedReferenceIndexes"] == [1, 2]
        assert compound_claim["referenceBinding"] == "local"
        assert compound_claim["faithfulness"] == "supported"
        assert compound_point["trustedAnchor"]["eligible"] is True

        supplement_only_analysis = json.loads(answer_level_analysis_path.read_text(encoding="utf-8"))
        supplement_only_claim = supplement_only_analysis["knowledgePoints"][0]["claims"]["doubao"]
        supplement_only_claim["faithfulness"] = "insufficient"
        supplement_only_claim["reason"] = "官方材料只支持连续缴存条件，不支持核心额度"
        supplement_only_claim["evidence"] = [
            {"referenceIndex": 2, "excerpt": "申请人需连续缴存满3个月"}
        ]
        supplement_only_path = out / "supplement-only-analysis.json"
        supplement_only_path.write_text(json.dumps(supplement_only_analysis, ensure_ascii=False), encoding="utf-8")
        supplement_only_result = out / "supplement-only-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(answer_level_source_path), "--analysis", str(supplement_only_path), "--output", str(supplement_only_result)])
        supplement_only_data = json.loads(supplement_only_result.read_text(encoding="utf-8"))
        supplement_only_normalized = supplement_only_data["knowledgePoints"][0]["claims"]["doubao"]
        assert supplement_only_normalized["referenceBinding"] == "answer_level_semantic"
        assert supplement_only_normalized["faithfulness"] == "supported"

        compressed_source = json.loads((FIXTURES / "results.json").read_text(encoding="utf-8"))
        compressed_source["platforms"][0]["answerMarkdown"] = "每人每月最高提取 2000 元 123。"
        compressed_source["platforms"][0]["references"] = [
            {"title": f"来源{index}", "url": f"https://example.gov.cn/{index}", "marker": str(index), "snippet": "每人每月最高提取2000元。"}
            for index in range(1, 4)
        ]
        compressed_path = out / "compressed-results.json"
        compressed_path.write_text(json.dumps(compressed_source, ensure_ascii=False), encoding="utf-8")
        compressed_analysis = json.loads((FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8"))
        compressed_claim = compressed_analysis["knowledgePoints"][0]["claims"]["dknowc-chat"]
        compressed_claim["claim"] = "每人每月最高提取2000元"
        compressed_claim["answerExcerpt"] = "每人每月最高提取 2000 元 123。"
        compressed_claim["citedReferenceIndexes"] = [1, 2, 3]
        compressed_claim["evidence"] = [{"referenceIndex": index, "excerpt": "每人每月最高提取2000元。"} for index in range(1, 4)]
        compressed_analysis_path = out / "compressed-analysis.json"
        compressed_analysis_path.write_text(json.dumps(compressed_analysis, ensure_ascii=False), encoding="utf-8")
        compressed_result = out / "compressed-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(compressed_path), "--analysis", str(compressed_analysis_path), "--output", str(compressed_result)])
        compressed_data = json.loads(compressed_result.read_text(encoding="utf-8"))
        assert compressed_data["knowledgePoints"][0]["claims"]["dknowc-chat"]["locallyBoundReferenceIndexes"] == [1, 2, 3]

        ambiguous_cluster_source = json.loads(
            (FIXTURES / "results.json").read_text(encoding="utf-8")
        )
        ambiguous_cluster_source["platforms"][0]["answerMarkdown"] = (
            "无合同租房每人每月最高可提取2000元12。"
        )
        ambiguous_cluster_source["platforms"][0]["references"] = [
            {
                "title": f"来源{index}",
                "url": f"https://example.gov.cn/{index}",
                "marker": str(index),
                "snippet": (
                    "无合同租房提取额度自2024年11月起为2000元/人/月。"
                    if index in (1, 2)
                    else "与当前额度无关的材料。"
                ),
            }
            for index in range(1, 13)
        ]
        ambiguous_cluster_path = out / "ambiguous-cluster-results.json"
        ambiguous_cluster_path.write_text(
            json.dumps(ambiguous_cluster_source, ensure_ascii=False),
            encoding="utf-8",
        )
        ambiguous_cluster_analysis = json.loads(
            (FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8")
        )
        ambiguous_cluster_claim = ambiguous_cluster_analysis[
            "knowledgePoints"
        ][0]["claims"]["dknowc-chat"]
        ambiguous_cluster_claim["claim"] = "无合同租房每人每月最高可提取2000元"
        ambiguous_cluster_claim["answerExcerpt"] = (
            ambiguous_cluster_source["platforms"][0]["answerMarkdown"]
        )
        ambiguous_cluster_claim["citedReferenceIndexes"] = [1, 2]
        ambiguous_cluster_claim["answerLevelReferenceIndexes"] = []
        ambiguous_cluster_claim["evidence"] = []
        ambiguous_cluster_analysis_path = out / "ambiguous-cluster-analysis.json"
        ambiguous_cluster_analysis_path.write_text(
            json.dumps(ambiguous_cluster_analysis, ensure_ascii=False),
            encoding="utf-8",
        )
        ambiguous_cluster_result = out / "ambiguous-cluster-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(ambiguous_cluster_path),
            "--analysis",
            str(ambiguous_cluster_analysis_path),
            "--output",
            str(ambiguous_cluster_result),
        ])
        ambiguous_cluster_data = json.loads(
            ambiguous_cluster_result.read_text(encoding="utf-8")
        )
        ambiguous_claim = ambiguous_cluster_data[
            "knowledgePoints"
        ][0]["claims"]["dknowc-chat"]
        assert ambiguous_claim["locallyBoundReferenceIndexes"] == [1, 2]
        assert ambiguous_claim["citedReferenceIndexes"] == [1, 2]
        assert ambiguous_claim["faithfulness"] == "supported"

        decimal_marker_source = json.loads(
            (FIXTURES / "results.json").read_text(encoding="utf-8")
        )
        decimal_marker_source["platforms"][0]["answerMarkdown"] = (
            "全日制从业人员小时最低工资标准为14.6元。"
        )
        decimal_marker_source["platforms"][0]["references"] = [
            {
                "title": "无关来源1",
                "url": "https://example.gov.cn/1",
                "marker": "1",
                "snippet": "与最低工资无关。",
            },
            {
                "title": "最低工资通知",
                "url": "https://example.gov.cn/2",
                "marker": "2",
                "snippet": "全日制从业人员小时最低工资标准为14.6元。",
            },
            {
                "title": "无关来源4",
                "url": "https://example.gov.cn/4",
                "marker": "4",
                "snippet": "与最低工资无关。",
            },
        ]
        decimal_marker_path = out / "decimal-marker-results.json"
        decimal_marker_path.write_text(
            json.dumps(decimal_marker_source, ensure_ascii=False),
            encoding="utf-8",
        )
        decimal_marker_analysis = json.loads(
            (FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8")
        )
        decimal_marker_claim = decimal_marker_analysis[
            "knowledgePoints"
        ][0]["claims"]["dknowc-chat"]
        decimal_marker_claim["claim"] = "全日制从业人员小时最低工资标准为14.6元"
        decimal_marker_claim["answerExcerpt"] = (
            decimal_marker_source["platforms"][0]["answerMarkdown"]
        )
        decimal_marker_claim["citedReferenceIndexes"] = []
        decimal_marker_claim["answerLevelReferenceIndexes"] = [2]
        decimal_marker_claim["evidence"] = []
        decimal_marker_analysis_path = out / "decimal-marker-analysis.json"
        decimal_marker_analysis_path.write_text(
            json.dumps(decimal_marker_analysis, ensure_ascii=False),
            encoding="utf-8",
        )
        decimal_marker_result = out / "decimal-marker-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(decimal_marker_path),
            "--analysis",
            str(decimal_marker_analysis_path),
            "--output",
            str(decimal_marker_result),
        ])
        decimal_marker_data = json.loads(
            decimal_marker_result.read_text(encoding="utf-8")
        )
        decimal_claim = decimal_marker_data[
            "knowledgePoints"
        ][0]["claims"]["dknowc-chat"]
        assert decimal_claim["locallyBoundReferenceIndexes"] == []
        assert decimal_claim["answerLevelReferenceIndexes"] == [2]
        assert decimal_claim["faithfulness"] == "supported"

        time_marker_source = json.loads(
            (FIXTURES / "results.json").read_text(encoding="utf-8")
        )
        time_marker_source["platforms"][0]["answerMarkdown"] = (
            "线下培训结束时间不得晚于20:30，"
            "线上培训结束时间不得晚于21:00【12】；"
        )
        time_marker_source["platforms"][0]["references"] = [
            {"title": "无关来源1", "url": "https://example.gov.cn/1", "marker": "1", "snippet": "无关材料。"},
            {"title": "无关来源2", "url": "https://example.gov.cn/2", "marker": "2", "snippet": "无关材料。"},
            {"title": "无关来源30", "url": "https://example.gov.cn/30", "marker": "30", "snippet": "无关材料。"},
            {
                "title": "校外培训管理规定",
                "url": "https://example.gov.cn/policy/12",
                "marker": "12",
                "snippet": "线下培训结束时间不得晚于20:30，线上培训结束时间不得晚于21:00。",
            },
        ]
        time_marker_path = out / "time-marker-results.json"
        time_marker_path.write_text(
            json.dumps(time_marker_source, ensure_ascii=False), encoding="utf-8"
        )
        time_marker_analysis = json.loads(
            (FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8")
        )
        time_marker_claim = time_marker_analysis["knowledgePoints"][0]["claims"]["dknowc-chat"]
        time_marker_claim["claim"] = "线下培训不晚于20:30，线上培训不晚于21:00"
        time_marker_claim["answerExcerpt"] = time_marker_source["platforms"][0]["answerMarkdown"]
        time_marker_claim["citedReferenceIndexes"] = [1, 2, 3, 4]
        time_marker_claim["answerLevelReferenceIndexes"] = []
        time_marker_claim["evidence"] = [{
            "referenceIndex": 4,
            "excerpt": "线下培训结束时间不得晚于20:30，线上培训结束时间不得晚于21:00。",
        }]
        time_marker_analysis_path = out / "time-marker-analysis.json"
        time_marker_analysis_path.write_text(
            json.dumps(time_marker_analysis, ensure_ascii=False), encoding="utf-8"
        )
        time_marker_result = out / "time-marker-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(time_marker_path),
            "--analysis",
            str(time_marker_analysis_path),
            "--output",
            str(time_marker_result),
        ])
        normalized_time_claim = json.loads(
            time_marker_result.read_text(encoding="utf-8")
        )["knowledgePoints"][0]["claims"]["dknowc-chat"]
        assert normalized_time_claim["locallyBoundReferenceIndexes"] == [4]
        assert normalized_time_claim["citedReferenceIndexes"] == [4]
        assert normalized_time_claim["referenceBinding"] == "local"
        assert normalized_time_claim["faithfulness"] == "supported"

        short_marker_source = json.loads((FIXTURES / "results.json").read_text(encoding="utf-8"))
        short_marker_source["platforms"][0]["answerMarkdown"] = (
            "数据互联校验通过的，无需提供证明材料；校验未通过的，"
            "需根据系统提示提供必要材料进行人工审核113。"
        )
        short_marker_source["platforms"][0]["references"] = [
            {"title": "来源1", "url": "https://example.com/1", "marker": "1", "snippet": "无关"},
            {"title": "来源3", "url": "https://example.com/3", "marker": "3", "snippet": "无关"},
            {
                "title": "直付房租试点通知",
                "url": "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/policyDetails?id=4973195",
                "marker": "113",
                "snippet": "数据互联校验通过的，无需提供证明材料；校验未通过的，需根据系统提示提供必要材料进行人工审核。",
            },
        ]
        short_marker_path = out / "short-marker-results.json"
        short_marker_path.write_text(json.dumps(short_marker_source, ensure_ascii=False), encoding="utf-8")
        short_marker_analysis = json.loads((FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8"))
        short_marker_claim = short_marker_analysis["knowledgePoints"][0]["claims"]["dknowc-chat"]
        short_marker_claim["claim"] = "数据校验通过免交证明材料，未通过时按提示补件"
        short_marker_claim["answerExcerpt"] = short_marker_source["platforms"][0]["answerMarkdown"]
        short_marker_claim["citedReferenceIndexes"] = []
        short_marker_claim["answerLevelReferenceIndexes"] = [3]
        short_marker_claim["faithfulness"] = "supported"
        short_marker_claim["reason"] = "局部脚标所附通知直接支持该主张"
        short_marker_claim["evidence"] = [{
            "referenceIndex": 3,
            "excerpt": "数据互联校验通过的，无需提供证明材料；校验未通过的，需根据系统提示提供必要材料进行人工审核",
        }]
        short_marker_analysis_path = out / "short-marker-analysis.json"
        short_marker_analysis_path.write_text(json.dumps(short_marker_analysis, ensure_ascii=False), encoding="utf-8")
        short_marker_result = out / "short-marker-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(short_marker_path), "--analysis", str(short_marker_analysis_path), "--output", str(short_marker_result)])
        short_marker_data = json.loads(short_marker_result.read_text(encoding="utf-8"))
        short_claim = short_marker_data["knowledgePoints"][0]["claims"]["dknowc-chat"]
        assert short_claim["locallyBoundReferenceIndexes"] == [3]
        assert short_claim["citedReferenceIndexes"] == [3]
        assert short_claim["answerLevelReferenceIndexes"] == []
        assert short_claim["referenceBinding"] == "local"
        assert short_claim["sourceLevel"] == "dknow_trusted_search_official"
        assert short_claim["reason"] == "逐段溯源；来源原文支持当前主张"

        verified_internal_source = json.loads(short_marker_path.read_text(encoding="utf-8"))
        verified_internal_source["platforms"][0]["references"][2]["resourceUrl"] = "https://gjj.beijing.gov.cn/official/4973195"
        verified_internal_path = out / "verified-internal-results.json"
        verified_internal_path.write_text(json.dumps(verified_internal_source, ensure_ascii=False), encoding="utf-8")
        verified_internal_result = out / "verified-internal-comparison.json"
        run([sys.executable, str(ROOT / "scripts" / "knowledge_compare.py"), "--input", str(verified_internal_path), "--analysis", str(short_marker_analysis_path), "--output", str(verified_internal_result)])
        verified_internal_data = json.loads(verified_internal_result.read_text(encoding="utf-8"))
        assert verified_internal_data["knowledgePoints"][0]["claims"]["dknowc-chat"]["sourceLevel"] == "dknow_trusted_search_official"

        internal_anchor_results = json.loads(
            (FIXTURES / "results.json").read_text(encoding="utf-8")
        )
        internal_reference = internal_anchor_results["platforms"][0]["references"][0]
        internal_reference["url"] = (
            "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/policyDetails?id=4973195"
        )
        for key in (
            "contentAcquisition",
            "sameMaterialVerified",
            "originAttributionStatus",
        ):
            internal_reference.pop(key, None)
        internal_reference["platformTrustSource"] = "dknow_reference_capture"
        internal_anchor_path = out / "internal-anchor-results.json"
        internal_anchor_path.write_text(
            json.dumps(internal_anchor_results, ensure_ascii=False), encoding="utf-8"
        )
        internal_anchor_output = out / "internal-anchor-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(internal_anchor_path),
            "--analysis",
            str(FIXTURES / "comparison-analysis.json"),
            "--output",
            str(internal_anchor_output),
        ])
        internal_anchor = json.loads(
            internal_anchor_output.read_text(encoding="utf-8")
        )
        assert internal_anchor["knowledgePoints"][0]["trustedAnchor"]["eligible"] is True

        external_anchor_results = json.loads(
            (FIXTURES / "results.json").read_text(encoding="utf-8")
        )
        external_reference = external_anchor_results["platforms"][0]["references"][0]
        for key in (
            "contentAcquisition",
            "sameMaterialVerified",
            "originAttributionStatus",
        ):
            external_reference.pop(key, None)
        external_reference["platformTrustSource"] = "dknow_reference_capture"
        external_anchor_path = out / "external-anchor-results.json"
        external_anchor_path.write_text(
            json.dumps(external_anchor_results, ensure_ascii=False),
            encoding="utf-8",
        )
        external_anchor_task = out / "external-anchor-task.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(external_anchor_path),
            "--task-output",
            str(external_anchor_task),
        ])
        external_task_reference = json.loads(
            external_anchor_task.read_text(encoding="utf-8")
        )["platforms"][0]["references"][0]
        assert (
            external_task_reference["platformTrustSource"]
            == "dknow_reference_capture"
        )
        external_anchor_output = out / "external-anchor-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(external_anchor_path),
            "--analysis",
            str(FIXTURES / "comparison-analysis.json"),
            "--output",
            str(external_anchor_output),
        ])
        external_anchor = json.loads(
            external_anchor_output.read_text(encoding="utf-8")
        )
        assert (
            external_anchor["knowledgePoints"][0]["trustedAnchor"]["eligible"]
            is True
        )
        assert (
            external_anchor["knowledgePoints"][0]["claims"]["dknowc-chat"][
                "sourceLevel"
            ]
            == "dknow_trusted_search_official"
        )
        forged_internal_results = json.loads(
            json.dumps(internal_anchor_results, ensure_ascii=False)
        )
        forged_internal_results["platforms"][0]["references"][0].pop(
            "platformTrustSource", None
        )
        forged_internal_path = out / "forged-internal-anchor-results.json"
        forged_internal_path.write_text(
            json.dumps(forged_internal_results, ensure_ascii=False),
            encoding="utf-8",
        )
        forged_internal_output = out / "forged-internal-anchor-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(forged_internal_path),
            "--analysis",
            str(FIXTURES / "comparison-analysis.json"),
            "--output",
            str(forged_internal_output),
        ])
        forged_internal = json.loads(
            forged_internal_output.read_text(encoding="utf-8")
        )
        assert forged_internal["knowledgePoints"][0]["trustedAnchor"]["eligible"] is True

        canonical_path = out / "canonical-analysis.json"
        canonical_output = out / "canonical-comparison.json"
        run([
            sys.executable,
            str(ROOT / "scripts" / "knowledge_compare.py"),
            "--input",
            str(FIXTURES / "results.json"),
            "--analysis",
            str(FIXTURES / "comparison-analysis.json"),
            "--canonical-analysis-output",
            str(canonical_path),
            "--output",
            str(canonical_output),
        ])
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        canonical_comparison = json.loads(canonical_output.read_text(encoding="utf-8"))
        assert canonical["schemaVersion"] == "fact-check-x/comparison-analysis@1"
        assert canonical["knowledgePoints"][0]["claims"] == canonical_comparison["knowledgePoints"][0]["claims"]
        assert canonical["knowledgePoints"][0]["trustedAnchor"] == canonical_comparison["knowledgePoints"][0]["trustedAnchor"]

        for case_name, excerpt in (
            ("unrelated", "本服务支持线上办理和进度查询。"),
            ("same-number-unrelated", "2024年全市共办理其他业务1400件。"),
            ("contradicted", "每人每月最高提取额度不是1400元，而是2000元。"),
        ):
            forged_results = json.loads(
                (FIXTURES / "results.json").read_text(encoding="utf-8")
            )
            forged_results["platforms"][0]["references"][0]["snippet"] = excerpt
            forged_analysis = json.loads(
                (FIXTURES / "comparison-analysis.json").read_text(encoding="utf-8")
            )
            forged_analysis["knowledgePoints"][0]["claims"]["dknowc-chat"]["evidence"] = [
                {"referenceIndex": 1, "excerpt": excerpt}
            ]
            forged_analysis["knowledgePoints"][0]["trustedAnchor"]["evidence"] = [
                {"referenceIndex": 1, "excerpt": excerpt}
            ]
            forged_results_path = out / f"{case_name}-anchor-results.json"
            forged_analysis_path = out / f"{case_name}-anchor-analysis.json"
            forged_output = out / f"{case_name}-anchor-comparison.json"
            forged_results_path.write_text(
                json.dumps(forged_results, ensure_ascii=False), encoding="utf-8"
            )
            forged_analysis_path.write_text(
                json.dumps(forged_analysis, ensure_ascii=False), encoding="utf-8"
            )
            run([
                sys.executable,
                str(ROOT / "scripts" / "knowledge_compare.py"),
                "--input",
                str(forged_results_path),
                "--analysis",
                str(forged_analysis_path),
                "--output",
                str(forged_output),
            ])
            forged = json.loads(forged_output.read_text(encoding="utf-8"))
            assert forged["knowledgePoints"][0]["trustedAnchor"]["eligible"] is False
            assert any(
                item.get("stage") == "comparison"
                for item in forged.get("analysisGaps") or []
            )
    if os.getenv("FACT_CHECK_X_ASSERTIONS_OUTPUT"):
        Path(os.environ["FACT_CHECK_X_ASSERTIONS_OUTPUT"]).write_text(json.dumps({
            "schemaVersion": "fact-check-x/test-assertions@1",
            "actualAssertionIds": [
                "contract.missing_fields_blocked",
                "contract.authority_not_entered",
                "citation.decimal_not_marker",
                "citation.time_not_marker",
                "source.answer_context_not_source_body",
                "deep_trace.independent_official_anchor",
            ],
        }), encoding="utf-8")
    print("PASS 知识点结构化对比")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
