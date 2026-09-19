import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { extractDknowcAnswer, validateCapturedAnswer } from "../assets/tool/dist/capture/generic-chat.js";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";

// 取自深知晓深度溯源页真实结构（2026-09 存档）：列表里先有一个 display:none 的进度机器人
// <li class="czkj-robot load-deep-search">…[检索完成]…</li>，再是可见的回答机器人。
// 正文尚未流出的空窗期，可见机器人的 .czkj-msg 只有界面元素；旧代码把它清空后回退到
// 隐藏进度面板，把 6 个字的“[检索完成]”当作最终答案并记为 success。
const BODY = "AI综合所有相关权威材料后，参考性解读如下，建议点击角标查看所依据的材料原文。国家高新技术企业认定须同时满足八项条件，其中企业申请认定时须注册成立一年以上，通过自主研发、受让、受赠、并购等方式获得对其主要产品在技术上发挥核心支持作用的知识产权的所有权。";

function fixture({ withBody }) {
    return `<!doctype html><meta charset="utf-8"><ul class="czkj-chat-center">
<li class="czkj-robot load-deep-search" style="display: none;"><div class="czkj-avatar czkj-robot-avatar"></div><div class="czkj-msg" style="background-color:#F7FAFF"><span class="img"></span> <span class="text">[检索完成]</span> <div class="loader-container"><div class="loader-bar"></div></div></div></li>
<li class="czkj-robot loading-header" style="display:none;">b</li>
<li class="czkj-robot chat-load-text" style="display:none !important;">b</li>
<li class="czkj-robot czkj-sse no-margin"><div class="czkj-avatar czkj-robot-avatar"></div><div class="czkj-msg"><div class="chatgpt-qa"><div class="chatsse-content" style="display:inline-block">
  <div class="chatsse-data chatSubBtn" style="display:flex"><span class="names chatSseName">已创建本问题知识专库（44条知识点）</span><span class="btns"></span></div>
  <div class="waitText hide" style="display:block"><span class="waitTextContent active">已完成思考（用时31.51秒）</span></div>
  <div class="reasoning_tip hide" style="display:block">已完成思考（用时31.51秒）</div>
  <div class="reasoning_value hide" style="display:none">用户问的是高新技术企业认定的核心条件。先看一下材料的整体情况，段落1、2都是官方文件……</div>
  <div class="timeText hide" style="display:none"></div>
  ${withBody ? `<span class="c1789799237718"><p>${BODY}<sup class="sup">302</sup></p></span>` : ""}
</div></div></div></li>
</ul>`;
}

const profileDir = await mkdtemp(join(tmpdir(), "fcx-dknow-placeholder-"));
const session = await openBrowserSession(profileDir, "about:blank", {});
let early;
let complete;
try {
    await session.page.setContent(fixture({ withBody: false }), { waitUntil: "domcontentloaded" });
    early = await extractDknowcAnswer(session.page);
    await session.page.setContent(fixture({ withBody: true }), { waitUntil: "domcontentloaded" });
    complete = await extractDknowcAnswer(session.page);
}
finally {
    await session.release();
    await rm(profileDir, { recursive: true, force: true });
}

assert.equal(early, "", `正文未流出时必须返回空串继续等待，而不是隐藏进度面板的文本；实际：${JSON.stringify(early)}`);
assert.ok(complete.startsWith("AI综合所有相关权威材料后"), `正文流出后必须返回正文；实际开头：${JSON.stringify(complete.slice(0, 40))}`);
assert.ok(complete.includes("【302】"), "脚标必须保留");
for (const forbidden of ["[检索完成]", "已完成思考", "已创建本问题知识专库", "用户问的是高新技术企业"]) {
    assert.ok(!complete.includes(forbidden), `回答不得混入界面元素或隐藏面板：${forbidden}`);
}

// 采集健全性门禁：进度占位或过短文本不得以 success 落盘。
const deep = { name: "dknowc-deep-research", label: "深知晓（深度溯源）" };
assert.equal(validateCapturedAnswer(deep, "[检索完成]")?.status, "failed", "占位符必须判失败");
assert.equal(validateCapturedAnswer(deep, "[查询]现行个人所得税法第六条")?.status, "failed", "进度文本必须判失败");
assert.equal(validateCapturedAnswer({ name: "dknowc-chat", label: "深知晓" }, "已完成思考")?.status, "failed", "过短文本必须判失败");
assert.equal(validateCapturedAnswer(deep, BODY), undefined, "完整正文必须放行");
assert.equal(validateCapturedAnswer({ name: "doubao", label: "豆包" }, "不需要提供居住证。"), undefined, "其他平台的短回答不受此门禁影响");

console.log("PASS 深度溯源正文未流出时不再回退到隐藏进度面板；占位/过短回答不记为成功");
