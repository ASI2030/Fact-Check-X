---
name: llm-answer-reference-compare
description: 用 Playwright 从豆包、深知晓、DeepSeek、Kimi、通义千问、腾讯元宝、ChatGPT、Claude、Gemini、智谱等网页端无损采集完整回答、原始引用、引用标记和现场存证。用于 Fact-Check-X 的 1.0 层，或任何需要比较多个 AI 网页回答与参考来源的任务。
---

# 1.0 多端回答与引用无损聚合

本技能只负责采集，不拆知识点、不判断真假，也不调用任何模型 API。可信搜索仅用于按平台已经给出的来源标题与原始 URL 补全该来源全文，不用于另找证据、替换原始来源或提前判断真假；语义工作由当前承载技能的智能体完成。

## 输出原则

- 完整保存平台原答案，不摘要、不改写、不截断。
- 按平台原顺序保存引用标题、原始 URL、标记、摘要与已捕获正文。
- 正文有引用标记时保留标记与引用顺序；没有标记时明确记录为无显式标记。
- 原始 URL 是存证主键。`normalizedUrl` 只用于去重，禁止用搜索结果替换原始 URL 或正文。
- 页面只显示来源名称但不暴露 URL 时，先尝试展开来源控件获取真实链接；仍无链接则写入 `sourceMentions`，明确标记为“无 URL 来源标签”，不得伪造成参考文献。
- 平台同时提供逐句脚标和“参考 N 篇资料”等全局来源列表时，两类都必须采集并用 `citationScope` 区分；同一 URL 合并时保留更完整的标题与摘录。
- 页面声明全局来源数量时，必须展开并达到该数量。未抓全视为采集失败，进入自动重采和 Computer Use 接管，禁止以残缺引用继续 1.1。
- 深知来源正文不足时，若安全环境已配置 `TRUSTED_SEARCH_KEY`，调用可信搜索并使用 `return_full_content=true`、`simplified=false` 补全与当前来源标题或 URL 匹配的同一材料全文；返回全文或段落直接用于判断，不再访问源网址二次抓取正文。可信搜索的 `源网址` 作为官方来源主链接，深知收录页作为辅助链接；不得把其他材料伪造成平台引用。
- 可信搜索未配置或未命中时，才直接读取来源页或提取 PDF；已绑定 PDF 仍拿不到可核验正文时必须失败关闭并进入正文提取、OCR 或 Computer Use 接管，不能把采集器的缺口判成平台幻觉。
- 登录、验证码、超时、页面结构变化和抽取失败必须成为显式状态，不能伪装成成功空回答。

## 准备运行时

```bash
cd assets/tool
npm ci --omit=dev
npx playwright install chromium
cd ../..
```

查看支持的平台：

```bash
node assets/tool/dist/cli.js platforms
```

首次使用某个平台时，先明确告诉用户“将打开浏览器，请完成登录，登录后再获取信息”。随后运行登录准备；程序检测到可提问界面后自动保存会话：

```bash
node assets/tool/dist/cli.js login --platform doubao
```

不得代替用户输入账号、密码、短信验证码或处理人机验证。登录会话保存在 `~/.fact-check-x/browser-profiles`，不得进入技能包或核验报告；已有持久化登录会话后可直接采集。

豆包等未登录页面即使显示输入框，也不得视为登录完成。必须等待页面登录入口消失并确认可提问界面后才能提交问题；登录入口仍可见时只等待用户登录，不得预先填入问题。

采集一个或更多平台。默认等待回答确认生成完成，并在失败后自动重采 2 次：

```bash
node assets/tool/dist/cli.js run \
  --question "<用户原始问题>" \
  --platform dknowc-chat \
  --platform doubao \
  --out <run目录> \
  --headed \
  --interactive \
  --timeout 180000 \
  --retries 2
```

平台参数可以重复任意 N 次（N≥1）。内置 `deepseek`；深知晓地址可用
`dknowc-chat=https://目标地址/` 覆盖，其他平台同理。例如：

```bash
node assets/tool/dist/cli.js run \
  --question "<用户原始问题>" \
  --platform dknowc-chat=https://测试5地址/ \
  --platform deepseek \
  --platform doubao \
  --out <run目录> \
  --headed --interactive
```

内置平台 `dknowc-deep-research` 对应
`https://poc1.dknowc.cn/wlcb/shenzhimini-test5/`。它不是普通单段采集：
采集器先提交原问题并等待普通回答完整生成，再点击回答下方“深度研究”入口，
接管新打开的可信溯源报告页，等待深度研究结果完整生成后提取答案、来源、截图
和页面存证。它与 `dknowc-chat` 是两个独立平台结果；只有 `dknowc-chat`
具备后续可信锚点免查资格。

所有选定平台必须成功，报告按实际平台数动态生成。

产物包括 `results.json`、1.0 对照报告、每个平台截图和页面存证。后续层只读取 `results.json`，不得回写原答案和原始来源。

采集命令最终返回非零状态，或任一平台不是 `success` 时，必须停止，不得进入知识点对比。失败后先根据 `capture-recovery.json` 调用运行载体的 Computer Use 接管可见 Chrome，直接复用其中的 `question`，不得要求用户回滚会话复制原问题。接管时保持当前页面，不得在等待人工验证期间关闭或重复打开浏览器；账号、密码、验证码和人机验证必须交给用户本人处理，并明确告诉用户可回复“验证已完成”或“答案已生成”。检测完成后继续检查当前页面，等待回答完全停止生成并自动续采；不得要求用户暂停或取消任务。

## 手工导入

网页自动化受阻时，可按 [数据契约](references/contracts.md) 手工整理 `results.json`，然后运行：

```bash
python3 scripts/validate_results.py --input <run目录>/results.json
node assets/tool/dist/cli.js report --input <run目录>/results.json --out <run目录>
```

## 交付门禁

```bash
python3 tests/smoke_test.py
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" .
```

完整验收口径见 [验收标准](references/acceptance-criteria.md)。
