import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { extractDknowcReferences } from "../assets/tool/dist/capture/generic-chat.js";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";

// 取自深知晓 2026-09 真实存证（临夏东乡低保题）：
//  1) 一份办事指南常常支撑多个脚标（【103】【104】【105】同指 guideDetails?id=14284289）。
//     只按 URL 合并会把后出现的脚标整条丢掉，这些脚标在回答里无法解析，
//     引用它们的主张被判证据不足并显示为「疑似误导」。
//  2) 同一个脚标又常常同时给出政务网原址与深知晓内部镜像 poc.dknowc.cn，
//     镜像标题是原文标题去掉发布单位前缀后的子串，这两条必须合并成一条，
//     且保留可独立核验的外部地址。
const GUIDE = "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/guideDetails?id=14284289";
const OTHER = "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/guideDetails?id=14280167";
const MIRROR = "https://poc.dknowc.cn/policy-model3-policy/#/guideDetails?id=14284289";
const ORIGIN = "https://zwfw.gansu.gov.cn/gszwdt/gszwdt/pages/onlinedeclaration/detail";

const card = (id, url, title, text) => `<div class="chat-jb">
  <div class="chat-jb-title"><span class="chat-jb-title-info a"><span class="chat-jb-title-text">${title}</span></span></div>
  <div class="chat-jb-content"><div class="jb-original">
    <div class="jb-original-item jb-note-score czkjNlpUrl" data-url2="${url}" data-text="${text}" data-id="${id}">
      <div class="scores-data"><span class="verticalMiddle">${id}</span><cite class="verticalMiddle">受理条件</cite></div>
    </div>
  </div></div>
</div>`;

const fixture = `<!doctype html><meta charset="utf-8"><div class="czkj-robot"><div class="czkj-msg">
<p>低保申请条件见指南<sup class="sup">103</sup><sup class="sup">104</sup><sup class="sup">105</sup>，办理材料另见<sup class="sup">106</sup>，受理时间见<sup class="sup">5</sup>。
  ${card("103", GUIDE, "【临夏回族自治州东乡族自治县】最低生活保障金的给付", "申请条件：共同生活的家庭成员人均收入低于当地低保标准。")}
  ${card("104", GUIDE, "【临夏回族自治州东乡族自治县】最低生活保障金的给付", "办理地点：东乡族自治县锁南镇锁南居委会。")}
  ${card("105", GUIDE, "【临夏回族自治州东乡族自治县】最低生活保障金的给付", "甘肃省-临夏回族自治州-东乡族自治县政务服务中心。")}
  ${card("106", OTHER, "【临夏回族自治州东乡族自治县】低保证核发", "一、低保证 来源渠道：政府部门核发。")}
  ${card("5", MIRROR, "【临夏回族自治州东乡族自治县】最低生活保障金的给付", "受理时间：法定工作日上午8:30-12:00。")}
  ${card("5", ORIGIN, "【甘肃省临夏回族自治州东乡县民政局】【临夏回族自治州东乡族自治县】最低生活保障金的给付", "受理时间：法定工作日上午8:30-12:00；下午14:30-18:00。")}
</p>
</div></div>`;

const profileDir = await mkdtemp(join(tmpdir(), "fcx-dknow-merge-"));
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

const markers = references.map((reference) => String(reference.marker));
// 1) 共享同一份文件的脚标必须各自保留，回答里的每个脚标都要能解析
for (const marker of ["103", "104", "105", "106", "5"]) {
    assert.ok(markers.includes(marker), `脚标 ${marker} 必须可解析；实际脚标集合 ${JSON.stringify(markers)}`);
}
const shared = references.filter((reference) => reference.normalizedUrl === references.find((r) => String(r.marker) === "103").normalizedUrl);
assert.equal(shared.length, 3, `同一份指南支撑的 3 个脚标必须各自成条，实际 ${shared.length}`);

// 2) 同一脚标的镜像与原址必须合并成一条，并保留可独立核验的外部地址
const five = references.filter((reference) => String(reference.marker) === "5");
assert.equal(five.length, 1, `脚标 5 的内部镜像与政务网原址必须合并为一条，实际 ${five.length}`);
assert.equal(five[0].url, ORIGIN, "合并后应保留政务网原址而不是 dknowc 内部镜像");

// 3) 不得出现重复脚标或空脚标
assert.equal(new Set(markers).size, markers.length, `不得出现重复脚标：${JSON.stringify(markers)}`);
for (const reference of references) {
    assert.ok(String(reference.marker ?? "").trim(), "每条引用都必须带脚标");
    assert.ok((reference.url || "").trim(), `脚标 ${reference.marker} 必须带来源链接`);
    assert.ok((reference.snippet || reference.traceabilityText || "").trim(), `脚标 ${reference.marker} 必须保留依据正文`);
}

console.log("PASS 一份来源支撑多个脚标时逐一保留；同一脚标的镜像与原址合并并保留外部地址");
