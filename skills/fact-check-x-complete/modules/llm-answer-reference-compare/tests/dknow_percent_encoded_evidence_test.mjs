import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { extractDknowcReferences } from "../assets/tool/dist/capture/generic-chat.js";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";

// 取自深知晓（深度溯源）2026-09 真实存证：办事指南卡片的 data-text 整体是百分号编码
// （同题普通问答不是），落盘后证据正文不可读，相关主张会被判证据不足。
// 编码串尾部可能被截断，整串 decodeURIComponent 会抛 URI malformed，必须逐片段解码。
const URL_A = "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/guideDetails?id=14284289";
const URL_B = "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/guideDetails?id=14280167";
const URL_C = "https://zwfw.gansu.gov.cn/gszwdt/pages/detail";

const PLAIN = "受理时间：法定工作日上午8:30-12:00。";
const ENCODED = encodeURIComponent("并于每月10日前通过社保卡发放低保金。");
// 尾部残缺的百分号片段：解不开时必须原样保留，不能连累整条正文
const TRUNCATED = encodeURIComponent("第二十五条 县级人民政府民政部门应当") + "%E4%BA";
const PERCENT_LITERAL = "研发费用占比不低于5%的企业适用本条";
const HTML_EVIDENCE = "<p>咨询电话：0930-7121766</p><div>工作日上午办理。</div>";
const ATTACHMENT_PREFIX = "https://example.gov.cn/download/policy.pdf 补贴期限最长不超过三年，期满后不再延长。";

const card = (id, url, text) => `<div class="chat-jb">
  <div class="chat-jb-title"><span class="chat-jb-title-text">【临夏回族自治州东乡族自治县】最低生活保障金的给付</span></div>
  <div class="chat-jb-content"><div class="jb-original">
    <div class="jb-original-item jb-note-score czkjNlpUrl" data-url2="${url}" data-text="${text}" data-id="${id}">
      <div class="scores-data"><span class="verticalMiddle">${id}</span><cite class="verticalMiddle">受理条件</cite></div>
    </div>
  </div></div>
</div>`;

const fixture = `<!doctype html><meta charset="utf-8"><div class="czkj-robot"><div class="czkj-msg">
<p>低保发放<sup class="sup">2</sup>，受理时间<sup class="sup">5</sup>，条例原文<sup class="sup">7</sup>，研发占比<sup class="sup">9</sup>，电话<sup class="sup">11</sup>，期限<sup class="sup">12</sup>。
  ${card("2", URL_A, ENCODED)}
  ${card("5", URL_B, PLAIN)}
  ${card("7", URL_C, TRUNCATED)}
  ${card("9", URL_C + "?x=1", PERCENT_LITERAL)}
  ${card("11", URL_C + "?x=2", HTML_EVIDENCE)}
  ${card("12", URL_C + "?x=3", ATTACHMENT_PREFIX)}
</p>
</div></div>`;

const profileDir = await mkdtemp(join(tmpdir(), "fcx-dknow-pct-"));
const session = await openBrowserSession(profileDir, "about:blank", {});
let references;
try {
    await session.page.setContent(fixture, { waitUntil: "domcontentloaded" });
    references = await extractDknowcReferences(session.page, "https://yun.dknowc.cn/wlcb/szx/#/", "低保怎么办理");
}
finally {
    await session.release();
    await rm(profileDir, { recursive: true, force: true });
}

const byMarker = new Map(references.map((reference) => [String(reference.marker), reference]));
const textOf = (marker) => byMarker.get(marker)?.traceabilityText || byMarker.get(marker)?.snippet || "";

// 1) 整体编码的证据正文必须解码成可读文本
assert.match(textOf("2"), /并于每月10日前通过社保卡发放低保金/, `脚标 2 的证据正文必须解码；实际 ${JSON.stringify(textOf("2").slice(0, 40))}`);
// 2) 本来就是明文的不得被改动
assert.match(textOf("5"), /受理时间：法定工作日上午8:30-12:00/, "明文证据不得被改动");
// 3) 尾部残缺不得导致整条失败：可解的部分要解开，解不开的片段原样保留
assert.match(textOf("7"), /第二十五条\s*县级人民政府民政部门应当/, `可解片段必须解开；实际 ${JSON.stringify(textOf("7").slice(0, 40))}`);
assert.ok(textOf("7").includes("%E4%BA"), "解不开的残缺片段必须原样保留，不得丢弃或报错");
// 4) 正文里真实存在的百分号不得被误当作编码
assert.match(textOf("9"), /研发费用占比不低于5%的企业适用本条/, `含百分号的正常文本不得被误解码；实际 ${JSON.stringify(textOf("9"))}`);
// 5) HTML 标签和附件下载地址不是证据正文，清洗后保留可审计文本
assert.equal(textOf("11"), "咨询电话：0930-7121766 工作日上午办理。", "HTML 证据应转成纯文本");
assert.equal(textOf("12"), "补贴期限最长不超过三年，期满后不再延长。", "附件地址前缀应从正文移除");
// 6) 任何一条证据都不得整体停留在编码态：允许残缺尾巴，但编码字符不得占据正文主体
for (const reference of references) {
    const text = reference.traceabilityText || reference.snippet || "";
    if (!text) {
        continue;
    }
    // 残余只允许是被截断的多字节字符尾巴（UTF-8 单字符最多 4 字节），
    // 超过这个量说明整段正文根本没有被解码。
    const residualUnits = (text.match(/%[0-9A-Fa-f]{2}/g) || []).length;
    assert.ok(
        residualUnits <= 3,
        `脚标 ${reference.marker} 残留 ${residualUnits} 个编码字节，超出截断尾巴的范围：${JSON.stringify(text.slice(0, 40))}`
    );
}

console.log("PASS 深知晓证据正文按片段解码；明文与真实百分号不受影响，残缺片段原样保留");
