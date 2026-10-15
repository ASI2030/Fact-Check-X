import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";
import { ReferenceSchema } from "../assets/tool/dist/schema/result.js";
import { extractDknowcReferences, extractReferences, extractSourcePageContentViaFetch } from "../assets/tool/dist/capture/generic-chat.js";

const longBody = "政策正文及申请条件。".repeat(1500) + "末条：有效期五年。";
const server = createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(`<title>完整来源</title><nav>网站导航</nav><main>${longBody}</main><footer>网站页脚</footer>`);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const out = await mkdtemp(join(tmpdir(), "fcx-feedback-"));
let session;
try {
    session = await openBrowserSession(join(out, "profile"), "about:blank", {});
    const page = session.page;
    await page.setContent(`<div class="jb"><span class="jb-txt">政策文件</span>
      <a class="jb-src" href="${origin}/policy">查看全文</a>
      <a class="jb-quote" href="${origin}/policy"><i class="jb-no">308</i><span>${longBody}</span></a>
      <a class="jb-quote" href="${origin}/policy"><i class="jb-no">309</i><span>另一个事实点的完整引用原文。</span></a></div>
      <div class="kb-item" data-url="${origin}/policy"><span class="kb-item-title">政策文件</span>
      <a class="jb-quote" href="${origin}/policy"><i class="jb-no">308</i><span>${longBody}</span></a></div>
      <a href="${origin}/missing-body">查看全文</a>`);
    const config = {name: "dknowc-deep-research", url: origin + "/chat", selectors: {references: ["a[href]"]}};
    const { references } = await extractReferences(config, page);
    assert.equal(references.filter((r) => r.marker === "308").length, 1);
    const first = references.find((r) => r.marker === "308");
    assert.equal(first.content, longBody);
    assert.equal(first.citationScope, "inline_and_global");
    assert.equal(first.title, "政策文件");
    assert.ok(references.some((r) => r.marker === "309"));
    const followed = references.find((r) => r.url.endsWith("/missing-body"));
    assert.equal(followed.sourceAcquisitionStatus, "captured");
    assert.equal(followed.content, longBody);
    assert.equal(ReferenceSchema.parse(first).sourceContentScope, "platform_excerpt");
    for (const kind of ["direct_docx_extraction", "direct_fetch_extraction"]) {
        assert.equal(ReferenceSchema.parse({...followed, contentAcquisition: kind}).contentAcquisition, kind);
    }
    const fetched = await extractSourcePageContentViaFetch(page, origin + "/policy");
    assert.equal(fetched.content, longBody);
    if (process.argv[2]) {
        await session.context.route("**/*", (route) => route.abort());
        const html = await readFile(process.argv[2], "utf8");
        await page.evaluate((value) => { document.documentElement.innerHTML = value; }, html);
        const replay = await extractDknowcReferences(page, "https://yun.dknowc.cn/");
        const observed = await page.locator(".jb-quote .jb-no").allTextContents();
        const markers = [...new Set(observed.map((s) => s.trim()).filter(Boolean))];
        assert.ok(markers.length > 0);
        assert.ok(markers.every((marker) => replay.some((r) => r.marker === marker && r.content)));
        console.log(JSON.stringify({ replayMarkers: markers.length, recoveredReferences: replay.length }));
    }
    console.log("PASS deep report markers, source bodies, hydration and untruncated extraction");
} finally {
    await session?.release();
    server.close();
    await rm(out, {recursive: true, force: true});
}
