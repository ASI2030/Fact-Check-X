---
name: fact-check-x-authoritative-verify
description: 对单个知识点调用可信搜索取得官方证据，由当前承载技能的智能体裁决各平台主张，并生成 V8 定稿事实核查报告。支持多知识点独立并发、深知晓可信搜索锚点免重复检索，以及直接准确、间接准确、结果巧合、严重误导、凭空编造等分类。用于 Fact-Check-X 云端权威核验，也可脱离 1.1 单独使用。
---

# 云端权威核验

本技能一次只核验一个知识点。多个知识点必须拆成多个独立请求并发执行，不能把完整回答或全部知识点塞进一个请求。

可信搜索只负责返回官方材料。知识点含义、证据是否支持主张和最终裁决由当前承载技能的智能体完成；技能内不调用模型 API。

## 单知识点取证

请求必须符合 [数据契约](references/contracts.md)。传给云端的用户内容只有：总标题、当前知识点，以及仅在各家说法不同时才出现的差异主张。

```bash
python3 scripts/authority_verify.py search \
  --request <K1-request.json> \
  --output <K1-evidence.json> \
  --service-area "<可选地区>"
```

若请求内有经 1.1 严格验收的 `trustedAnchor.eligible=true`，程序直接复用深知晓所附官方证据，输出 `searchMode=dknow_exempt`、`requestCount=0`。否则只调用一次可信搜索，输出 `searchMode=trusted_search`、`requestCount=1`。

通过 Fact-Check-X 统一入口调用时，可信搜索配置由跨载体配置组件自动注入：用户首次只需登录深知 MaaS，组件自动获取或创建专用 Key；以后 Codex、Claude Code、WorkBuddy 等直接复用本机共享配置。批量执行前仍会检查所有请求。存在非免查知识点且当前进程没有收到可用 Key 时，程序会在任何搜索开始前失败。不得让用户在对话中粘贴 Key，也不得仅因深知晓附带官方来源就绕过可信搜索；只有明确的 `trustedAnchor.eligible=true` 才可免查。

## 并行取证

```bash
python3 scripts/batch_search.py \
  --requests-dir <authority-requests> \
  --output-dir <authority-evidence> \
  --max-workers 12 \
  --service-area "<可选地区>"
```

11 个知识点会形成 11 个互不依赖的任务并行执行；其中有深知晓权威锚点的任务不发云端请求。

## 当前智能体裁决

阅读单点请求和证据后，当前智能体写出：

当 `searchMode=dknow_exempt` 时，`request.trustedAnchor.officialAnswer` 是当前知识点的权威结论，证据列表承担来源追溯作用，不要求标题或截断摘录逐字复述该结论。各平台主张与 `officialAnswer` 语义一致或可由其直接推出时，必须裁决为 `supported` 并引用当前锚点中的有效证据 ID；只有主张增加了权威结论不能支持的实质事实，或确实无法判定时，才使用 `insufficient`。

1.1 已独立保存平台引用忠实性。本阶段只裁决事实正确性：平台自己的引用不充分但结论被权威锚点证实时，仍裁决为 `supported`，最终分类由程序结合原忠实性形成 `coincidental`。

```json
{
  "authoritativeFinding": "官方证据支持的有界结论",
  "verdicts": {
    "doubao": {
      "verdict": "supported",
      "reason": "主张与官方证据一致",
      "evidenceIds": ["E1"]
    }
  }
}
```

`authoritativeFinding` 必须非空；每个已覆盖平台都必须有裁决，`verdict` 只能是 `supported`、`contradicted` 或 `insufficient`；`reason` 必须非空；`supported` 和 `contradicted` 必须至少引用一个当前证据包中真实存在的 `evidenceId`。程序不接受顶层 `verdict`、`officialAnswer` 或 `platformAssessment`，也不会把错误结构静默降级为待复核。

若可信搜索正常返回但没有取得权威材料，输出 `no_evidence` 并进入 `needs_review`。没有检索到材料不构成对主张的反证，禁止自动归类为“编造”或标记完成。

然后验收：

```bash
python3 scripts/authority_verify.py finalize \
  --request <K1-request.json> \
  --evidence <K1-evidence.json> \
  --assessment <K1-assessment.json> \
  --output <K1-result.json>
```

分类规则：

- 所附官方材料忠实且权威核验正确：`direct_accurate`。
- 所附非官方材料忠实，且独立权威核验正确：`indirect_accurate`。
- 没有自己的可靠依据，但结果碰巧正确：`coincidental`。
- 权威证据证明结果错误：`misleading`。
- 可信搜索成功但官方查无：`fabricated`。
- 服务错误或证据仍不足：`unverified`。
- 未覆盖：`omitted`。

官方材料给出全部前提，平台只做一步显然算术推导时，可由当前智能体在 1.1 判为忠实，最终仍是 `direct_accurate`。

“所附材料”既可以由当前主张片段内的逐段溯源建立，也可以在没有局部脚标时，由 1.1 对本次回答明确返回的参考资料做全文语义溯源。逐段溯源优先；已有局部脚标时不得用回答后段或回答级官方来源抬级。最终报告必须把 `local` 与 `answer_level_semantic` 分开显示，不能把“无逐句角标”写成“无来源”。

## V8 定稿报告

`verification.json` 必须先生成独立云端权威核验报告：

```bash
python3 scripts/render_authority_report.py \
  --verification <verification.json> \
  --output <03-authority-report.html>
```

该报告逐知识点展示权威结论、权威证据、核验方式，以及本次用户所选全部平台
的主张、裁决理由和证据绑定。平台集合必须与 `verification.json` 完全一致，
缺少任一已选平台裁决时拒绝生成；平台数量由输入决定，只要求 `N≥1`。`N=1` 为单平台权威核验，`N≥2` 同时保留跨平台差异。
报告顶部必须先展示与全部知识点 ID 完整绑定的 `finalAnswer`，作为本阶段可直接
使用的“权威核验后的最终答案”；存在待复核项时不得标成已核验完成。

汇总后的 `verification.json` 可直接生成 V8 定稿报告：

```bash
python3 scripts/render_v8_report.py \
  --results <results.json> \
  --comparison <comparison.json> \
  --verification <verification.json> \
  --output <事实核查报告.html>
```

视觉和章节结构以 [V8 定稿报告视觉基准](references/V8定稿报告视觉基准.html) 为准。

## 交付门禁

```bash
python3 tests/smoke_test.py
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" .
```
