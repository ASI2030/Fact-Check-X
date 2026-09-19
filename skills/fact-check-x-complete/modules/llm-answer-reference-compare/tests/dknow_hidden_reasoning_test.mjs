import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { extractDknowcAnswer } from "../assets/tool/dist/capture/generic-chat.js";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";

// 取自深知晓 2026-09 问答页真实结构：回答容器里依次是知识专库按钮、
// 「已完成思考」状态头、折叠的推理面板（display:none）、然后才是正文。
// 克隆节点后 innerText 不再应用 display:none，旧代码会把整段推理当作回答采集。
const REASONING = "我们 need answer。需要根据检索材料回答深圳夫妻投靠入户条件。这可能是市内移居？";
const BODY_LEAD = "AI综合所有相关权威材料后，参考性解读如下";

function fixture({ reasoningStyle }) {
    return `<!doctype html><meta charset="utf-8"><div class="czkj-chat-center">
<div class="czkj-robot czkj-welcome-msg"><div class="czkj-msg">您好，我是深知晓，很高兴为您服务</div></div>
<div class="czkj-robot"><div class="czkj-msg"><div class="chatgpt-qa"><div class="chatsse-content" style="display:inline-block">
  <div class="chatsse-data chatSubBtn" style="display:flex"><span class="names chatSseName">已创建本问题知识专库（20条知识点）</span><span class="btns"></span></div>
  <div class="waitText"><span class="waitTextContent">已完成思考</span></div>
  <div class="reasoning_tip hide" style="display: block;">已完成思考（用时8.68秒）</div>
  <div class="reasoning_value active" style="${reasoningStyle}">${REASONING}</div>
  <!---->
  <span class="c1789533169627nbjuzq">
    <p>${BODY_LEAD}，建议点击角标查看所依据的<span style="font-weight:bold">材料原文</span>。</p>
    <h2>先说明适用口径</h2>
    <p>市外迁入夫妻投靠：较新的专门规定是2016年户籍迁入规定<sup class="sup">107</sup>。
      <div class="chat-jb"><div class="chat-jb-title">来源卡片标题不属于正文</div></div>
    </p>
    <p style="display:none">被样式隐藏的段落不得采集</p>
    <p hidden>被 hidden 属性隐藏的段落不得采集</p>
  </span>
</div></div></div></div>
</div>`;
}

const profileDir = await mkdtemp(join(tmpdir(), "fcx-dknow-hidden-"));
const session = await openBrowserSession(profileDir, "about:blank", {});
const answers = {};
try {
    for (const [name, reasoningStyle] of [["collapsed", "display: none;"], ["expanded", "display: block;"]]) {
        await session.page.setContent(fixture({ reasoningStyle }), { waitUntil: "domcontentloaded" });
        answers[name] = await extractDknowcAnswer(session.page);
    }
}
finally {
    await session.release();
    await rm(profileDir, { recursive: true, force: true });
}

for (const [name, answer] of Object.entries(answers)) {
    assert.ok(answer.includes(BODY_LEAD), `${name}: 必须采集到回答正文`);
    assert.ok(answer.includes("先说明适用口径"), `${name}: 正文小标题必须保留`);
    assert.ok(answer.includes("【107】"), `${name}: 脚标必须转成【107】`);
    assert.ok(!answer.includes("我们 need answer"), `${name}: 推理面板不得混入回答（用户看不到的内容）`);
    assert.ok(!answer.includes("已完成思考"), `${name}: 思考状态头与用时提示（深度溯源页可见的 .reasoning_tip）不属于回答`);
    assert.ok(!answer.includes("已创建本问题知识专库"), `${name}: 知识专库按钮不属于回答`);
    assert.ok(!answer.includes("来源卡片标题不属于正文"), `${name}: 来源卡片仍须剔除`);
    assert.ok(!answer.includes("被样式隐藏的段落"), `${name}: display:none 的正文节点不得采集`);
    assert.ok(!answer.includes("被 hidden 属性隐藏的段落"), `${name}: hidden 属性节点不得采集`);
    assert.ok(!answer.includes("很高兴为您服务"), `${name}: 欢迎语不属于回答`);
    assert.ok(answer.startsWith(BODY_LEAD), `${name}: 回答必须从正文开始，实际开头：${JSON.stringify(answer.slice(0, 40))}`);
}

console.log("PASS 深知晓回答不再混入隐藏推理面板、状态头与知识专库按钮");
