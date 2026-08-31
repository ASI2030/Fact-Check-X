import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { captureGenericChat } from "../assets/tool/dist/capture/generic-chat.js";
import { resolveVisibleBrowserExecutable } from "../assets/tool/dist/capture/browser-session.js";

let origin = "";
const server = createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    response.setHeader("content-type", "text/html; charset=utf-8");
    if (url.pathname === "/source/official") {
        response.end(`<!doctype html><title>官方政策原文</title><main>这是已打开的官方来源正文。政策明确规定：申请人符合条件时可以办理该业务，本页内容专用于验证引用正文存证。</main>`);
        return;
    }
    if (url.pathname === "/source/blocked") {
        response.statusCode = 403;
        response.end("Access denied");
        return;
    }
    if (url.pathname === "/deepseek") {
        response.end(chatPage(
            "ds-assistant-message-main-content",
            `<p>回答中的主张只是上下文<a href="${origin}/source/official"><span class="ds-markdown-cite">[7]</span></a>。</p>
             <p>另一条来源无法访问<a href="${origin}/source/blocked"><span class="ds-markdown-cite">[11]</span></a>。</p>`
        ));
        return;
    }
    if (url.pathname === "/yuanbao") {
        response.end(chatPage(
            "",
            `元宝回答中引用了来源 3。`,
            `<div class="hyc-common-markdown__ref_card" data-idx="3" data-url="${origin}/source/official">
               <div class="hyc-common-markdown__ref_card-title">官方政策卡片</div>
               <div class="hyc-common-markdown__ref_card-foot__source_txt">政府网站</div>
             </div>`,
            `<div class="t-dialog__position" id="guide"><div class="auto-search-guide-popup__button">我知道了</div></div>
             <style>.t-dialog__position{position:fixed;inset:0;z-index:9;background:rgba(255,255,255,.2)}</style>
             <script>document.querySelector('.auto-search-guide-popup__button').onclick=()=>document.querySelector('#guide').remove()</script>`
        ));
        return;
    }
    response.statusCode = 404;
    response.end("not found");
});

function chatPage(answerClass, answerHtml, sourceHtml = "", overlayHtml = "") {
    return `<!doctype html><textarea id="q"></textarea><button id="send">发送</button><div class="${answerClass}" id="answer"></div><div id="sources"></div>${overlayHtml}
      <script>
        document.querySelector('#send').onclick = () => {
          document.querySelector('#q').value = '';
          document.querySelector('#answer').innerHTML = ${JSON.stringify(answerHtml)};
          document.querySelector('#sources').innerHTML = ${JSON.stringify(sourceHtml)};
        };
      </script>`;
}

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
origin = `http://127.0.0.1:${server.address().port}`;
const out = await mkdtemp(join(tmpdir(), "fact-check-x-source-capture-"));

async function capture(name, label, path) {
    return captureGenericChat({
        name,
        label,
        adapter: "generic-chat",
        url: `${origin}${path}`,
        profile: `source-capture-${name}-${Date.now()}`,
        requiresLogin: false,
        completionStableMs: 100,
        selectors: {
            input: ["#q"],
            submit: ["#send"],
            answer: ["#answer"],
            references: []
        }
    }, {
        question: "验证引用正文存证",
        outDir: out,
        headed: false,
        interactive: false,
        executablePath: resolveVisibleBrowserExecutable(),
        launchTimeoutMs: 120000,
        timeoutMs: 10000,
        loginTimeoutMs: 10000
    });
}

try {
    const deepseek = await capture("deepseek", "DeepSeek", "/deepseek");
    assert.equal(deepseek.status, "success");
    assert.deepEqual(deepseek.references.map((item) => item.marker), ["7", "11"]);
    const captured = deepseek.references[0];
    assert.equal(captured.answerContext.includes("回答中的主张"), true);
    assert.equal(captured.snippetProvenance, "source_document");
    assert.equal(captured.sourceAcquisitionStatus, "captured");
    assert.equal(captured.content.includes("已打开的官方来源正文"), true);
    assert.equal(deepseek.references[1].sourceAcquisitionStatus, "blocked");
    assert.equal(deepseek.references[1].answerContext.includes("无法访问"), true);

    const yuanbao = await capture("yuanbao", "腾讯元宝", "/yuanbao");
    assert.equal(yuanbao.status, "success");
    assert.equal(yuanbao.references.length, 1);
    assert.equal(yuanbao.references[0].marker, "3");
    assert.equal(yuanbao.references[0].sourceAcquisitionStatus, "captured");
    assert.equal(yuanbao.references[0].snippetProvenance, "source_document");
    assert.equal(yuanbao.references[0].content.includes("政策明确规定"), true);
} finally {
    server.close();
    await rm(out, { recursive: true, force: true });
}

if (process.env.FACT_CHECK_X_ASSERTIONS_OUTPUT) {
    await writeFile(process.env.FACT_CHECK_X_ASSERTIONS_OUTPUT, JSON.stringify({
        schemaVersion: "fact-check-x/test-assertions@1",
        actualAssertionIds: [
            "capture.deepseek_source_body",
            "capture.yuanbao_source_body",
            "capture.source_failure_states_distinct"
        ]
    }), "utf8");
}

console.log("PASS DeepSeek/元宝引用链接逐条打开、正文存证与受阻状态区分");
