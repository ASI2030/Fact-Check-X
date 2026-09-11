import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { extractDknowcReferences } from "../assets/tool/dist/capture/generic-chat.js";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";

// 取自深知晓新版问答页真实存证：来源链接挂在条目自身的 data-url2，
// 而同一张卡片里的「一键办理」按钮带的 data-url 是一张图片，不能当来源。
const GUIDE_URL = "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/guideDetails?id=12363581";
const POLICY_URL = "https://yun.dknowc.cn/wlcb/ShenZhi-policy/#/policyDetails?id=349961";
const EXTERNAL_URL = "https://www.sz.gov.cn/hdjl/ywzsk/gaj/hz/content/post_11149765.html";
const LEGACY_URL = "https://yun.dknowc.cn/baike/policyDetails?id=legacy-12";
const ASSET_URL = "https://gdldrk.gdga.gd.gov.cn/dttp/SZAppoint.png";

const fixture = `<!doctype html><meta charset="utf-8"><div class="czkj-robot"><div class="czkj-msg">
<p>被投靠一方须属于深圳市（县）户口<sup class="sup">105</sup><sup class="sup">106</sup>。
  <div class="chat-jb">
    <div class="chat-jb-title"><span class="chat-jb-title-info a"><span class="chat-jb-title-text"><span class="chat-jb-title-tag">【深圳市公安局】</span>【深圳市】市内移居-投靠配偶</span></span></div>
    <div class="chat-jb-content"><div class="jb-original">
      <span class="chat-jb-content-span bold">溯源原文:<span class="chat-jb-title-btn czkjNlpUrl" data-url="${ASSET_URL}">一键办理</span></span>
      <div class="jb-original-item jb-note-score czkjNlpUrl" data-url2="${GUIDE_URL}" data-text="受理条件：被投靠一方属于市、县户口，其配偶可以申请户口迁移与被投靠一方合成一户。" data-id="105">
        <div class="scores-data"><span class="verticalMiddle">105</span><cite class="verticalMiddle">受理条件</cite></div>
      </div>
    </div></div>
  </div>
  <div class="chat-jb">
    <div class="chat-jb-title"><span class="chat-jb-title-info a"><span class="chat-jb-title-text"><span class="chat-jb-title-tag">【2016】</span>深圳市人民政府关于印发深圳市户籍迁入若干规定的通知</span></span></div>
    <div class="chat-jb-content"><div class="jb-original">
      <div class="jb-original-item jb-note-score czkjNlpUrl" data-url2="${POLICY_URL}" data-text="（二）夫妻一方为深圳市户籍的，其配偶按下列要求申请投靠入户：分居时间满2年后可以申请入户。" data-id="106">
        <div class="scores-data"><span class="verticalMiddle">106</span><cite class="verticalMiddle">第十条 政策性迁户条件</cite></div>
      </div>
    </div></div>
  </div>
</p>
<p>另见办理材料说明<sup class="sup">202</sup>。
  <div class="chat-jb">
    <div class="chat-jb-title"><span class="chat-jb-title-info a"><span class="chat-jb-title-text">投靠亲属家庭户申请材料</span></span></div>
    <div class="chat-jb-content"><div class="jb-original">
      <div class="jb-original-item jb-note-score czkjNlpUrl" data-url="${EXTERNAL_URL}" data-text="投靠亲属家庭户申请材料清单。" data-id="202">
        <div class="scores-data"><span class="verticalMiddle">202</span></div>
      </div>
    </div></div>
  </div>
</p>
<p>历史版本页面结构<sup class="sup">12</sup>。
  <div class="chatsse-note-item" data-id="0195e286e1288df69a21187a8340e382">
    <span class="chatsse-note-score-id" data-id="12">12</span>
    <span class="czkjTitle">校外培训管理规定</span>
    <span data-url="${LEGACY_URL}"></span>
    <span class="scoresText">旧版来源卡片必须继续被采集。</span>
  </div>
</p>
</div></div>`;

const profileDir = await mkdtemp(join(tmpdir(), "fcx-dknow-marker-"));
const session = await openBrowserSession(profileDir, "about:blank", {});
let references;
try {
    await session.page.setContent(fixture, { waitUntil: "domcontentloaded" });
    references = await extractDknowcReferences(
        session.page,
        "https://yun.dknowc.cn/wlcb/szx/#/",
        "深圳夫妻投靠入户条件"
    );
}
finally {
    await session.release();
    await rm(profileDir, { recursive: true, force: true });
}

const byMarker = new Map(references.map((reference) => [reference.marker, reference]));
assert.deepEqual(
    [...byMarker.keys()].sort(),
    ["105", "106", "12", "202"].sort(),
    "回答中出现的每个脚标都必须有对应来源"
);

// 图片等资源链接不得作为来源，脚标与 URL 必须来自同一张卡片
for (const reference of references) {
    assert.doesNotMatch(
        reference.url || "",
        /\.(?:png|jpe?g|gif|svg|webp|bmp|ico)(?:[?#]|$)/i,
        `脚标 ${reference.marker} 不得把图片资源当作来源`
    );
}
assert.equal(byMarker.get("105").url, GUIDE_URL);
assert.equal(byMarker.get("106").url, POLICY_URL);
assert.equal(byMarker.get("202").url, EXTERNAL_URL);
assert.equal(byMarker.get("12").url, LEGACY_URL);

// 哈希路由是深知晓来源的身份，不能在归一化时被抹掉
assert.notEqual(byMarker.get("105").normalizedUrl, byMarker.get("106").normalizedUrl);
assert.match(byMarker.get("105").normalizedUrl, /#\/guideDetails/);

// 溯源原文必须随引用留存
for (const reference of references) {
    assert.ok(
        (reference.snippet || "").trim().length > 0,
        `脚标 ${reference.marker} 必须保留依据正文`
    );
}
assert.match(byMarker.get("106").traceabilityText || "", /分居时间满2年/);
assert.equal(byMarker.get("105").sourceSection, "受理条件");
assert.match(byMarker.get("12").title, /校外培训管理规定/);

console.log("PASS 深知晓引用脚标全覆盖、来源同卡片、溯源原文保留");
