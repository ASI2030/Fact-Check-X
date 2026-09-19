import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { builtInPlatforms } from "../assets/tool/dist/capture/platform-registry.js";
import { captureGenericChat } from "../assets/tool/dist/capture/generic-chat.js";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";
import { PlatformStatusSchema } from "../assets/tool/dist/schema/result.js";
import { captureWithRetries } from "../assets/tool/dist/cli.js";

// 2026-09 豆包把提问框换成 TipTap/ProseMirror 编辑器：
// <div contenteditable="true" class="tiptap ProseMirror"> —— 没有 role=textbox，也没有 textarea。
const LEGACY_DOUBAO_INPUT = ["[contenteditable='true'][role='textbox']", "div[role='textbox']", "textarea.semi-input-textarea", ".semi-input-textarea", "textarea"];
const DOUBAO_PAGE = `<!doctype html><meta charset="utf-8">
<aside><button>新对话</button><button>登录</button></aside>
<main><div class="composer"><div contenteditable="true" class="tiptap ProseMirror" translate="no" style="min-height:44px;padding:10px;border:1px solid #ccc"><p><br></p></div></div></main>`;

async function firstVisible(page, selectors) {
    for (const selector of selectors) {
        const locator = page.locator(selector);
        const count = await locator.count().catch(() => 0);
        for (let index = count - 1; index >= 0; index -= 1) {
            if (await locator.nth(index).isVisible().catch(() => false)) {
                return locator.nth(index);
            }
        }
    }
    return null;
}

const doubao = builtInPlatforms.find((platform) => platform.name === "doubao");
const workRoot = await mkdtemp(join(tmpdir(), "fcx-doubao-tiptap-"));
process.env.FACTCHECK_BROWSER_PROFILE_DIR = join(workRoot, "profiles");
try {
    // A. 当前豆包适配器必须能定位 TipTap 输入框；旧选择器必须定位不到（证明这是回归）。
    const session = await openBrowserSession(join(workRoot, "probe"), "about:blank", {});
    try {
        await session.page.setContent(DOUBAO_PAGE, { waitUntil: "domcontentloaded" });
        const legacy = await firstVisible(session.page, LEGACY_DOUBAO_INPUT);
        assert.equal(legacy, null, "旧选择器不应匹配 TipTap 输入框，否则本测试不再具备回归意义");
        const current = await firstVisible(session.page, doubao.selectors.input);
        assert.ok(current, "当前豆包适配器必须找到 TipTap 输入框");
        const className = await current.evaluate((node) => node.className);
        assert.match(className, /\btiptap\b/, "命中的必须是 TipTap 编辑器节点");
    }
    finally {
        await session.release();
    }

    // B. 找不到输入框时必须给出独立状态，不能伪装成登录问题。
    const fixtureUrl = "data:text/html;charset=utf-8," + encodeURIComponent(
        "<!doctype html><meta charset=\"utf-8\"><main><h1>示例助手</h1><p>页面已就绪，但界面结构已变化。</p></main>"
    );
    const outDir = join(workRoot, "out");
    await mkdir(outDir, { recursive: true });
    const result = await captureGenericChat({
        name: "fixture-chat",
        label: "夹具助手",
        url: fixtureUrl,
        adapter: "generic-chat",
        profile: "fixture-status",
        requiresLogin: false,
        selectors: { input: ["textarea.does-not-exist"], send: ["button"], answer: ["main"] }
    }, { outDir, timeoutMs: 2500, loginTimeoutMs: 1000, headed: false, interactive: false, question: "测试问题" });
    assert.equal(result.status, "input_not_found", `期望 input_not_found，实际 ${result.status}: ${result.error}`);
    assert.match(result.error || "", /输入框/, "错误信息必须说明是找不到输入框");
    assert.doesNotMatch(result.error || "", /请.*登录|完成登录|重新登录/, "错误信息不得引导用户去登录");
    assert.equal(PlatformStatusSchema.parse("input_not_found"), "input_not_found");

    // C. 该状态同样停止机械重采（重采不会让消失的选择器回来）。
    let calls = 0;
    const retried = await captureWithRetries(doubao, { retryCount: 2, timeoutMs: 1000, retryDelayMs: 1 }, async () => {
        calls += 1;
        return { platform: "doubao", label: "豆包", status: "input_not_found", error: "未找到可见的提问输入框" };
    }, async () => undefined);
    assert.equal(retried.status, "input_not_found");
    assert.equal(calls, 1, "input_not_found 不应机械重采");
}
finally {
    await rm(workRoot, { recursive: true, force: true });
}

console.log("PASS 豆包 TipTap 输入框定位；找不到输入框时报 input_not_found 而非登录问题");
