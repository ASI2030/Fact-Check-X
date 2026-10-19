---
name: fact-check-x-knowledge-compare
description: 在本地把多个 AI 原始回答拆成原子知识点，区分直接答案与补充参考，对齐各平台主张、差异、所附依据和来源忠实性，并生成可视化中间报告。不调用可信搜索或任何外部模型 API，语义分析由当前运行载体完成。
license: Apache-2.0
---

# 知识点结构化对比

本技能是独立、可选的本地中间层。它回答“各家说了什么、哪里相同或不同、各自有没有依据、是否忠实于自己的依据”，不回答外部世界中谁最终正确。

当前承载技能的智能体（Codex、Claude Code 等）直接完成知识点拆解和语义判断。脚本只生成任务包、校验证据约束、标准化结构并渲染报告，不调用模型 API。

## 第一步：生成分析任务包

```bash
python3 scripts/knowledge_compare.py \
  --input <run>/results.json \
  --task-output <run>/comparison-task.json
```

阅读 `comparison-task.json` 后，由当前智能体写出 `comparison-analysis.json`。必须遵守：

- 先读紧凑任务包。`capturedText` 只是预览；预览不够时按 `sourceResultsPath` 回读该来源全文，不限制为固定三次工具调用。
- 来源只有平台片段时，由模型判断是否需要补全同一原来源；可用采集器 `hydrate-sources` 命令。保留原结果，不换来源、不重提问题；补全后从新采集版本重新比较。

- 合并所有成功平台中可独立核验的事实，每个知识点只表达一个事实变量。
- 原子性同时约束知识点和各平台 `claim`；原句包含多个独立义务、条件、对象、数值或后果时必须拆点，每个 `claim` 只保留当前事实，`answerExcerpt` 可以复用原句。
- 金额换算、比较符号、简称、算术推导和基准/优惠关系由模型结合原文理解，在理由中说明。是否拆点取决于业务含义，不用固定数值、关键词或字符串相似度决定真假。
- 仅个别平台提出的实质新增事实也要单独成点，其他平台标为未覆盖；不得把新增事实并入宽泛知识点后借用深知晓锚点免查。
- 同一事实的不同数值或条件放在同一知识点下，不拆成两点。
- `direct` 采用最小充分原则：缺失后就没有直接回答用户明确所问内容；`reference` 是背景、提醒、延伸或相邻业务信息。问全国时省市案例、问城市时区县细项均属于补充参考，除非用户明确询问地区差异；只问条件/门槛/比例时，奖励、流程、电话等不进入直接答案。
- 每个平台逐点写明主张、所附依据和来源忠实性。
- 优先保留实际脚标对应关系。引用其他回答级材料时如实标明，不伪装为局部脚标，不改写原来源身份。
- 同一平台的全局来源清单可作为判断材料；是否足以支撑当前主张由模型说明，不因缺少局部脚标自动判巧合。
- 比较阶段使用当前捕获的原答案和来源；补全也只针对同一原来源。独立检索新证据属于后续事实核验阶段，不能倒填为平台自带引用。
- 若确认深知晓该知识点使用可信搜索且所附官方证据支持同一个原子事实，可声明 `trustedAnchor`；锚点只能覆盖该事实变量，超出部分必须另起无锚点知识点，由下游可信搜索核验。
- 同一知识点若普通深知晓和深度溯源各自附有支持其主张的官方原文，必须全部保留到 `trustedAnchor.claimEvidenceMap`；后续权威核验直接复用，不得因追加搜索未召回同一材料而降为证据不足。
- 顶层必须生成 `synthesisDraft`：`status` 固定为 `unverified`，正文基于全部相关知识点合并并保留冲突、条件和缺口，`basisKnowledgePointIds` 只能引用当前知识点。它是独立可交付的“综合草案（未核验）”，不能冒充权威最终答案。

## 第二步：验收并渲染

```bash
python3 scripts/knowledge_compare.py \
  --input <run>/results.json \
  --analysis <run>/comparison-analysis.json \
  --output <run>/comparison.json

python3 scripts/render_comparison.py \
  --results <run>/results.json \
  --comparison <run>/comparison.json \
  --output <run>/comparison.html
```

`comparison.html` 按实际平台数展示全部原始答案与来源、知识点对比概览和
“综合草案（未核验）”；下方展示逐知识点完整对照。

## 交付门禁

```bash
python3 tests/smoke_test.py
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" .
```

数据形状见 [数据契约](references/contracts.md)，分类和引用规则见 [验收标准](references/acceptance-criteria.md)。
