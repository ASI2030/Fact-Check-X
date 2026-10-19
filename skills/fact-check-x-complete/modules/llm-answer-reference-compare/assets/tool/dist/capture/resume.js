import { createHash } from "node:crypto";
import { join } from "node:path";
import { authenticationRequired, waitForAuthentication } from "./auth-state.js";
import { openBrowserSession } from "./browser-session.js";
import { extractReferences, extractDoubaoAnswer, extractDoubaoSourceMentions, validateCapturedAnswer, waitForAnswer } from "./generic-chat.js";
import { profileDirectory } from "../utils/profile.js";
import { ensureDir, writeTextFile } from "../utils/filesystem.js";

const questionSelectors = {
    "doubao": "[data-testid='send_message']",
    "deepseek": ".ds-message .ds-collapsible-text",
    "dknowc-chat": ".czkj-user .czkj-msg",
    "dknowc-deep-research": "#hero-title"
};

export async function assertResumeQuestion(page, config, question, conversationUrl, timeoutMs = 30000) {
    if (page.url() !== conversationUrl) {
        throw new Error("原会话地址已跳转，拒绝在其他会话提取回答。");
    }
    const selector = config.selectors?.question || questionSelectors[config.name];
    if (!selector) throw new Error("该平台尚无原问题定位器，需先完成页面适配。");
    const questions = page.locator(selector);
    const last = questions.last();
    await last.waitFor({ state: "visible", timeout: timeoutMs }).catch(() => undefined);
    if (!(await last.isVisible().catch(() => false))) {
        throw new Error("未定位到原会话中可见的用户问题；未提交任何内容。");
    }
    const actual = (await last.innerText()).replace(/^问题[：:]\s*/, "").replace(/\s+/g, "");
    if (actual !== question.replace(/\s+/g, "")) {
        throw new Error("当前会话最后一个问题与原任务不一致；拒绝错采或重新提问。");
    }
}

export async function resumeCaptureOnPage(config, page, options) {
    await assertResumeQuestion(page, config, options.question, options.conversationUrl, Math.min(options.timeoutMs, 60000));
    let answerMarkdown = await waitForAnswer(config, page, options.timeoutMs, "", options.question, {
        interactive: Boolean(options.interactive),
        verificationTimeoutMs: options.loginTimeoutMs,
        resumingExistingAnswer: true
    });
    const validation = validateCapturedAnswer(config, answerMarkdown);
    if (validation) throw new Error(validation.error);
    await assertResumeQuestion(page, config, options.question, options.conversationUrl);
    const extraction = await extractReferences(config, page, options.question);
    if (config.name === "doubao") {
        answerMarkdown = await extractDoubaoAnswer(page, extraction.inlineMarkers);
        const finalValidation = validateCapturedAnswer(config, answerMarkdown);
        if (finalValidation) throw new Error(finalValidation.error);
    }
    const sourceMentions = config.name === "doubao"
        ? (await extractDoubaoSourceMentions(page)).filter(item => !extraction.references.some(ref => ref.text === item.label))
        : [];
    return { answerMarkdown, references: extraction.references, sourceMentions, sourceCountAudit: extraction.sourceCountAudit };
}

export async function resumeExistingCapture(config, prior, options) {
    const started = Date.now();
    const lifecycle = { ...prior.captureLifecycle, questionSha256: createHash("sha256").update(options.question).digest("hex"),
        conversationUrl: options.conversationUrl, resubmissionAllowed: false,
        recoverySubmissionCount: 0, recoveryCount: (prior.captureLifecycle?.recoveryCount || 0) + 1 };
    const result = { platform: prior.platform, label: prior.label, url: prior.url,
        status: "failed", answerMarkdown: "", references: [], sourceMentions: [], captureLifecycle: lifecycle };
    const originalUrl = new URL(options.conversationUrl);
    const platformUrl = new URL(config.url);
    if (!/^https?:$/.test(originalUrl.protocol) || originalUrl.origin !== platformUrl.origin) {
        throw new Error("恢复地址必须属于原平台；不能切换平台或浏览器身份。");
    }
    if (originalUrl.href.replace(/\/$/, "") === platformUrl.href.replace(/\/$/, "")) {
        throw new Error("平台首页不能唯一标识原会话；请提供原会话或深度溯源报告的专用URL。");
    }
    if (prior.captureLifecycle?.questionSha256 && prior.captureLifecycle.questionSha256 !== lifecycle.questionSha256) {
        throw new Error("原问题摘要不一致，拒绝恢复。");
    }
    config._currentQuestion = options.question;
    let session;
    let page;
    try {
        session = await openBrowserSession(profileDirectory(config.profile), options.conversationUrl, options);
        page = session.page;
        page.setDefaultTimeout(options.timeoutMs);
        await page.goto(options.conversationUrl, { waitUntil: "domcontentloaded", timeout: options.timeoutMs });
        if (config.requiresLogin && await authenticationRequired(page, config)) {
            if (!options.interactive || !await waitForAuthentication(page, config, options.loginTimeoutMs)) {
                result.status = "login_required";
                throw new Error("原会话需要本人完成登录；未重新提交问题。");
            }
        }
        Object.assign(result, await resumeCaptureOnPage(config, page, options));
        result.status = "success";
        lifecycle.answerObserved = true;
    } catch (error) {
        result.error = String(error?.message || error);
    } finally {
        if (page) {
            const dir = join(options.outDir, "artifacts", config.name);
            await ensureDir(dir);
            try {
                await page.screenshot({ path: join(dir, "screenshot.png"), fullPage: true });
                await writeTextFile(join(dir, "page.html"), await page.content());
                result.artifacts = { screenshot: `artifacts/${config.name}/screenshot.png`, html: `artifacts/${config.name}/page.html` };
            } catch { /* A closed page remains an explicit failed recovery. */ }
        }
        await session?.release();
    }
    result.durationMs = Date.now() - started;
    return result;
}
