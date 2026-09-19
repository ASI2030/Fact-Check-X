import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join, relative, win32 } from "node:path";
import { promisify } from "node:util";
import { authenticationRequired, waitForAuthentication } from "./auth-state.js";
import { openBrowserSession } from "./browser-session.js";
import { ensureDir, writeTextFile } from "../utils/filesystem.js";
import { normalizeUrl } from "../utils/urls.js";
import { profileDirectory } from "../utils/profile.js";
const execFileAsync = promisify(execFile);
const trustedSearchContentCache = new Map();
let localTrustedSearchKey;

async function trustedSearchKey() {
    if (localTrustedSearchKey !== undefined) {
        return localTrustedSearchKey;
    }
    const path = String(
        process.env.FACT_CHECK_X_TRUSTED_SEARCH_KEY_FILE
        || join(homedir(), ".fact-check-x", "credentials", "trusted-search-key")
    ).trim();
    try {
        const candidate = String(await readFile(path, "utf8")).trim();
        localTrustedSearchKey = candidate.length >= 16 && !/\s|\*/.test(candidate)
            ? candidate
            : "";
    }
    catch {
        localTrustedSearchKey = "";
    }
    if (localTrustedSearchKey) {
        return localTrustedSearchKey;
    }
    const environmentKey = String(process.env.TRUSTED_SEARCH_KEY || "").trim();
    return environmentKey.length >= 16 && !/\s|\*/.test(environmentKey)
        ? environmentKey
        : "";
}

export async function captureGenericChat(config, options) {
    const started = Date.now();
    const profileDir = profileDirectory(config.profile);
    const artifactDir = join(options.outDir, "artifacts", config.name);
    await ensureDir(artifactDir);
    let session;
    let context;
    let page;
    let capturedAnswerMarkdown = "";
    let capturedReferences = [];
    let capturedSourceMentions = [];
    let capturedSourceCountAudit;
    try {
        if (options.headed && options.interactive) {
            console.log(`${config.label}：将打开浏览器。请先完成登录或验证；检测到可提问界面后会自动继续采集。`);
        }
        session = await openBrowserSession(profileDir, config.url, options);
        context = session.context;
        page = session.page;
        const activePage = page;
        page.setDefaultTimeout(options.timeoutMs);
        page.setDefaultNavigationTimeout(options.timeoutMs);
        await page.goto(config.url, {
            waitUntil: "domcontentloaded",
            timeout: options.timeoutMs
        });
        if (config.requiresLogin) {
            await page.waitForTimeout(1500);
        }
        if (options.headed && options.interactive) {
            console.log(`${config.label} 浏览器已打开，正在等待登录状态和提问界面。`);
        }
        let loginRequired = config.requiresLogin
            ? await authenticationRequired(page, config)
            : false;
        if (config.requiresLogin && !loginRequired) {
            loginRequired = !(await waitForAuthentication(
                page,
                config,
                Math.min(options.loginTimeoutMs || 300000, 5000)
            ));
        }
        if (loginRequired) {
            if (!options.headed || !options.interactive) {
                return failure(
                    config,
                    "login_required",
                    started,
                    "页面显示登录入口；未确认登录前禁止提交问题。",
                    await saveArtifacts(page, artifactDir, options.outDir)
                );
            }
            console.log(`${config.label} 当前未登录。请在浏览器完成登录；登录入口消失后将自动继续，期间不会提交问题。`);
            const authenticated = await waitForAuthentication(page, config, options.loginTimeoutMs || 300000);
            if (!authenticated) {
                return failure(
                    config,
                    "login_required",
                    started,
                    "等待登录超时；未向平台提交问题。",
                    await saveArtifacts(page, artifactDir, options.outDir)
                );
            }
            console.log(`${config.label} 已确认登录状态，开始定位提问界面。`);
        }
        const inputSelectors = config.selectors?.input || [
            "textarea",
            "[contenteditable='true']",
            "input[type='text']"
        ];
        const readySelectors = chatReadySelectors(inputSelectors);
        let input = await waitForFirstVisible(page, readySelectors, Math.min(options.timeoutMs, 20000));
        const gated = await pageLooksLikeGate(page);
        if (!input && !gated) {
            input = await waitForFirstVisible(page, inputSelectors, Math.min(options.timeoutMs, 5000));
        }
        if (!input && options.headed && options.interactive) {
            console.log(`${config.label} 未在页面上找到提问输入框；页面可能仍在加载或界面结构已变化。若页面需要人工操作请在浏览器处理，检测到输入框后将自动继续。`);
            input = await waitForFirstVisible(page, readySelectors, options.loginTimeoutMs || 300000);
        }
        if (gated && !input) {
            return failure(config, "login_required", started, "Page appears to require login, verification, or human interaction.", await saveArtifacts(page, artifactDir, options.outDir));
        }
        if (!input) {
            return failure(config, "input_not_found", started, "未找到可见的提问输入框；页面未显示登录入口，界面结构可能已变化，需要更新该平台适配器的输入框选择器。", await saveArtifacts(page, artifactDir, options.outDir));
        }
        if (config.requiresLogin && !(await waitForAuthentication(
            page,
            config,
            Math.min(options.loginTimeoutMs || 300000, 5000)
        ))) {
            if (!options.headed || !options.interactive) {
                return failure(
                    config,
                    "login_required",
                    started,
                    "登录状态未连续稳定，未向平台提交问题。",
                    await saveArtifacts(page, artifactDir, options.outDir)
                );
            }
            console.log(`${config.label} 登录状态尚未稳定。请在当前 Playwright 页面完成登录；检测稳定后会自动继续。`);
            const authenticated = await waitForAuthentication(page, config, options.loginTimeoutMs || 300000);
            if (!authenticated) {
                return failure(
                    config,
                    "login_required",
                    started,
                    "等待稳定登录状态超时；未向平台提交问题。",
                    await saveArtifacts(page, artifactDir, options.outDir)
                );
            }
        }
        if (config._testCloseBeforeSubmitOnce) {
            config._testCloseBeforeSubmitOnce = false;
            await page.close();
            throw new Error("采集页面已关闭；将重开页面并自动重放原问题。");
        }
        const doubaoConversationIdsBefore = config.name === "doubao"
            ? await collectDoubaoConversationIds(page)
            : [];
        if (config.name === "yuanbao") {
            await dismissYuanbaoGuides(page);
        }
        const previousAnswer = await extractAnswer(config, page);
        await input.click();
        await fillPrompt(input, options.question);
        let submission = await submitPromptAndConfirm(config, activePage, input, options.question, previousAnswer, options);
        if (submission === "verification_required") {
            const verificationHandled = await handleVerificationIfNeeded(config, page, options);
            if (!verificationHandled) {
                return failure(config, "verification_required", started, "Page requires captcha, image verification, or other human verification.", await saveArtifacts(page, artifactDir, options.outDir));
            }
            input = await waitForFirstVisible(page, readySelectors, Math.min(options.timeoutMs, 10000)) || input;
            submission = await submitPromptAndConfirm(config, activePage, input, options.question, previousAnswer, options);
        }
        if (submission === "verification_required") {
            return failure(config, "verification_required", started, "Page still requires captcha, image verification, or other human verification.", await saveArtifacts(page, artifactDir, options.outDir));
        }
        if (submission !== "submitted") {
            return failure(config, "failed", started, "已执行发送操作，但页面未出现输入清空、生成状态或新回答，拒绝把点击成功误判为问题已提交。", await saveArtifacts(page, artifactDir, options.outDir));
        }
        if (config.name === "doubao") {
            await activateDoubaoSubmittedConversation(
                page,
                doubaoConversationIdsBefore,
                previousAnswer,
                Math.min(options.timeoutMs, 15000)
            );
        }
        let answerMarkdown = await waitForAnswer(config, page, options.timeoutMs, previousAnswer, options.question, {
            interactive: Boolean(options.headed && options.interactive),
            verificationTimeoutMs: options.loginTimeoutMs || 300000
        });
        if (config.name === "dknowc-chat" && options.deepCompanionConfig) {
            const ordinaryValidation = validateCapturedAnswer(config, answerMarkdown);
            if (ordinaryValidation) {
                return failure(
                    config,
                    ordinaryValidation.status,
                    started,
                    ordinaryValidation.error,
                    await saveArtifacts(page, artifactDir, options.outDir)
                );
            }
            const extraction = await extractReferences(config, page, options.question);
            const references = extraction.references;
            const artifacts = await saveArtifacts(page, artifactDir, options.outDir);
            const ordinaryResult = success(
                config,
                started,
                answerMarkdown,
                references,
                [],
                artifacts
            );
            ordinaryResult.companionResult = await captureDknowcDeepCompanion(
                page,
                context,
                options.deepCompanionConfig,
                options,
                answerMarkdown
            );
            return ordinaryResult;
        }
        if (config.adapter === "dknowc-deep-research") {
            page = await activateDknowcDeepResearch(
                page,
                context,
                config,
                Math.min(options.timeoutMs, 20000),
                answerMarkdown
            );
            page.setDefaultTimeout(options.timeoutMs);
            page.setDefaultNavigationTimeout(options.timeoutMs);
            const deepResearchTimeoutMs = Number(config.deepResearchTimeoutMs)
                || options.timeoutMs;
            answerMarkdown = await waitForAnswer(
                config,
                page,
                deepResearchTimeoutMs,
                "",
                options.question,
                {
                    interactive: Boolean(options.headed && options.interactive),
                    verificationTimeoutMs: options.loginTimeoutMs || 300000
                }
            );
        }
        capturedAnswerMarkdown = answerMarkdown;
        const extraction = await extractReferences(config, page, options.question);
        const references = extraction.references;
        capturedReferences = references;
        capturedSourceCountAudit = extraction.sourceCountAudit;
        const sourceMentions = config.name === "doubao"
            ? (await extractDoubaoSourceMentions(page))
                .filter((mention) => !references.some((reference) => reference.text === mention.label))
            : [];
        capturedSourceMentions = sourceMentions;
        const artifacts = await saveArtifacts(page, artifactDir, options.outDir);
        const validation = validateCapturedAnswer(config, answerMarkdown);
        if (validation) {
            return {
                ...failure(config, validation.status, started, validation.error, artifacts),
                artifacts
            };
        }
        return success(
            config,
            started,
            answerMarkdown,
            references,
            sourceMentions,
            artifacts,
            capturedSourceCountAudit
        );
    }
    catch (error) {
        const status = error && typeof error === "object" && "captureStatus" in error
            ? error.captureStatus
            : "failed";
        const references = error && typeof error === "object" && Array.isArray(error.partialReferences)
            ? error.partialReferences
            : capturedReferences;
        const answerMarkdown = capturedAnswerMarkdown;
        const sourceMentions = capturedSourceMentions.length > 0
            ? capturedSourceMentions
            : config.name === "doubao" && page
                ? (await extractDoubaoSourceMentions(page).catch(() => []))
                    .filter((mention) => !references.some((reference) => reference.text === mention.label))
                : [];
        return failure(
            config,
            status,
            started,
            error instanceof Error ? error.message : String(error),
            page ? await saveArtifacts(page, artifactDir, options.outDir).catch(() => undefined) : undefined,
            {
                answerMarkdown,
                references,
                sourceMentions,
                sourceCountAudit: capturedSourceCountAudit
            }
        );
    }
    finally {
        await session?.release();
    }
}
function chatReadySelectors(selectors) {
    return selectors.filter((selector) => !selector.includes("input[type='text']"));
}
async function firstVisible(page, selectors) {
    for (const selector of selectors) {
        const locator = page.locator(selector);
        const count = await locator.count().catch(() => 0);
        for (let index = count - 1; index >= 0; index -= 1) {
            const candidate = locator.nth(index);
            if (await candidate.isVisible().catch(() => false)) {
                return candidate;
            }
        }
    }
    return null;
}
async function waitForFirstVisible(page, selectors, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (page.isClosed?.()) {
            throw new Error("采集页面已关闭；将重开页面并自动重放原问题。");
        }
        const visible = await firstVisible(page, selectors);
        if (visible) {
            return visible;
        }
        await page.waitForTimeout(500);
    }
    return firstVisible(page, selectors);
}
async function fillPrompt(locator, question) {
    await locator.fill(question).catch(async () => {
        await locator.evaluate((node, value) => {
            if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
                node.value = value;
                node.dispatchEvent(new Event("input", { bubbles: true }));
                node.dispatchEvent(new Event("change", { bubbles: true }));
            }
            else {
                node.textContent = value;
                node.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true }));
                node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
                node.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
            }
        }, question);
    });
    const currentValue = await locator.evaluate((node) => {
        if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
            return node.value;
        }
        return node.textContent || "";
    }).catch(() => "");
    if (!currentValue.includes(question.slice(0, Math.min(10, question.length)))) {
        await locator.click();
        await locator.type(question, { delay: 5 });
    }
}
async function clickInputContainerBottomRight(page, input) {
    const point = await input.evaluate((node) => {
        const element = node;
        const container = element.closest("#input-engine-container")
            || element.closest("[class*='input-content-container']")
            || element.parentElement?.parentElement?.parentElement
            || element;
        const rect = container.getBoundingClientRect();
        return {
            x: rect.left + rect.width - 28,
            y: rect.top + rect.height - 28
        };
    }).catch(() => null);
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
        return false;
    }
    await page.mouse.click(point.x, point.y).catch(() => undefined);
    return true;
}
export async function submitPromptAndConfirm(config, page, input, question, previousAnswer, options = {}) {
    const initial = await confirmPromptSubmission(config, page, input, question, previousAnswer, 500);
    if (initial !== "unconfirmed") {
        return initial;
    }
    const strategies = [];
    const send = await firstVisible(page, config.selectors?.send || [
        "button[type='submit']",
        "button:has-text('Send')",
        "button:has-text('发送')",
        "button"
    ]);
    if (send) {
        strategies.push(() => send.click().then(() => true).catch(() => false));
    }
    if (config.sendFallback === "input-container-bottom-right") {
        strategies.push(() => clickInputContainerBottomRight(page, input));
    }
    strategies.push(() => page.keyboard.press("Enter").then(() => true).catch(() => false));
    const transitionTimeoutMs = Number(options.submissionTimeoutMs) || 5000;
    for (const strategy of strategies) {
        const attempted = await strategy();
        if (!attempted) {
            continue;
        }
        const state = await confirmPromptSubmission(config, page, input, question, previousAnswer, transitionTimeoutMs);
        if (state !== "unconfirmed") {
            return state;
        }
    }
    if (options.headed && options.interactive) {
        console.log(`${config.label} 尚未确认问题已送出。请在当前 Playwright 页面完成验证或手工发送；无需回滚会话复制问题，也不要暂停或取消任务。检测到生成状态或答案后会自动继续采集。`);
        return confirmPromptSubmission(
            config,
            page,
            input,
            question,
            previousAnswer,
            options.loginTimeoutMs || 300000
        );
    }
    return "unconfirmed";
}
export async function confirmPromptSubmission(config, page, input, question, previousAnswer = "", timeoutMs = 5000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (await pageLooksLikeVerification(page)) {
            return "verification_required";
        }
        if (await isGenerationInProgress(config, page)) {
            return "submitted";
        }
        const answer = await extractAnswer(config, page);
        if (answer
            && answer !== previousAnswer
            && !looksLikeQuestionEcho(answer, question)
            && !looksLikeNonAnswerPrompt(answer)) {
            return "submitted";
        }
        const inputValue = await readInputValue(input);
        if (question
            && typeof inputValue === "string"
            && !normalizeComparableText(inputValue).includes(normalizeComparableText(question))) {
            return "submitted";
        }
        await page.waitForTimeout(250);
    }
    return "unconfirmed";
}
async function readInputValue(input) {
    return input.evaluate((node) => {
        if (node instanceof HTMLTextAreaElement || node instanceof HTMLInputElement) {
            return node.value;
        }
        return node.textContent || "";
    }).catch(() => null);
}
async function extractAnswer(config, page) {
    if (config.name === "deepseek") {
        const answer = await extractDeepSeekAnswer(page);
        if (answer) {
            return answer;
        }
    }
    if (config.name === "doubao") {
        const answer = await extractDoubaoAnswer(page);
        if (answer) {
            return answer;
        }
    }
    if (config.name === "yuanbao") {
        const answer = await extractYuanbaoAnswer(page);
        if (answer) {
            return answer;
        }
    }
    if (isDknowcPlatform(config)) {
        return extractDknowcAnswer(page);
    }
    if (config.name === "kimi") {
        return extractKimiAnswer(page);
    }
    const selectors = config.selectors?.answer || [
        "[data-message-author-role='assistant']",
        "[class*='assistant']",
        "[class*='answer']",
        "[class*='message']",
        "main"
    ];
    for (const selector of selectors) {
        const locator = page.locator(selector).last();
        if ((await locator.count()) > 0) {
            const text = (await locator.innerText().catch(() => "")).trim();
            if (text && !looksLikeLoadingText(text)) {
                return normalizeAnswerText(text);
            }
        }
    }
    return "";
}
function normalizeAnswerText(text) {
    return text.replace(/(https?:\/\/[^\s\u4e00-\u9fff]+)/g, "$1 ");
}
async function extractDeepSeekAnswer(page) {
    const locator = page.locator(".ds-assistant-message-main-content").last();
    if ((await locator.count().catch(() => 0)) === 0) {
        return "";
    }
    const text = await locator.evaluate((node) => {
        const clone = node.cloneNode(true);
        for (const anchor of Array.from(clone.querySelectorAll("a[href]"))) {
            const marker = anchor.querySelector(".ds-markdown-cite")?.textContent?.replace(/\D+/g, "");
            anchor.replaceWith(document.createTextNode(marker ? `[${marker}]` : ""));
        }
        return clone.innerText.trim();
    }).catch(() => "");
    if (!text || looksLikeLoadingText(text)) {
        return "";
    }
    return normalizeAnswerText(text);
}
async function extractDoubaoAnswer(page) {
    const locator = page.locator(".md-box-root").last();
    if ((await locator.count().catch(() => 0)) === 0) {
        return "";
    }
    const text = await locator.evaluate((node) => {
        const clone = node.cloneNode(true);
        const sourceMarkers = new Map();
        for (const source of Array.from(clone.querySelectorAll(".container-DEV3jt, .container-sWvQla"))) {
            const label = source.textContent?.trim().replace(/\s+/g, " ");
            if (!label) {
                source.remove();
                continue;
            }
            if (!sourceMarkers.has(label)) {
                sourceMarkers.set(label, String(sourceMarkers.size + 1));
            }
            source.replaceWith(document.createTextNode(`【${sourceMarkers.get(label)}】`));
        }
        clone.querySelectorAll(".spacing-ANk22f").forEach((element) => element.remove());
        return clone.innerText.trim();
    }).catch(() => "");
    if (!text || looksLikeLoadingText(text)) {
        return "";
    }
    return normalizeAnswerText(text);
}
async function collectDoubaoConversationIds(page) {
    return page
        .locator("a[id^='conversation_'][href^='/chat/']")
        .evaluateAll((nodes) => nodes
        .map((node) => node.id)
        .filter(Boolean))
        .catch(() => []);
}
export async function activateDoubaoSubmittedConversation(
    page,
    previousConversationIds = [],
    previousAnswer = "",
    timeoutMs = 15000
) {
    const known = new Set(previousConversationIds);
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const answer = await extractDoubaoAnswer(page);
        if (answer && answer !== previousAnswer) {
            return false;
        }
        const conversations = page.locator("a[id^='conversation_'][href^='/chat/']");
        const count = await conversations.count().catch(() => 0);
        for (let index = 0; index < count; index += 1) {
            const candidate = conversations.nth(index);
            const id = await candidate.getAttribute("id").catch(() => "");
            if (!id || known.has(id)) {
                continue;
            }
            const href = await candidate.getAttribute("href").catch(() => "");
            const currentPath = new URL(page.url()).pathname;
            const candidatePath = href?.startsWith("/")
                ? href
                : href
                    ? new URL(href, page.url()).pathname
                    : "";
            if (candidatePath && currentPath === candidatePath) {
                return false;
            }
            await candidate.click({ timeout: 3000 });
            await page.waitForTimeout(1000);
            return true;
        }
        await page.waitForTimeout(300);
    }
    return false;
}
export async function extractDoubaoSourceMentions(page) {
    const root = page.locator(".md-box-root").last();
    const labels = await root
        .locator(".container-DEV3jt, .container-sWvQla")
        .evaluateAll((nodes) => nodes
        .map((node) => node.textContent?.trim().replace(/\s+/g, " ") || "")
        .filter(Boolean))
        .catch(() => []);
    const mentions = new Map();
    for (const label of labels) {
        const existing = mentions.get(label);
        if (existing) {
            existing.occurrenceCount += 1;
            continue;
        }
        mentions.set(label, {
            label,
            marker: String(mentions.size + 1),
            occurrenceCount: 1
        });
    }
    return [...mentions.values()];
}
async function extractYuanbaoAnswer(page) {
    const locator = page.locator(".agent-chat__list__item--ai .hyc-common-markdown").last();
    if ((await locator.count().catch(() => 0)) === 0) {
        return "";
    }
    const text = await locator.evaluate((node) => {
        const clone = node.cloneNode(true);
        for (const refList of Array.from(clone.querySelectorAll(".hyc-common-markdown__ref-list"))) {
            const idxList = refList.querySelector("[data-idx-list]")?.getAttribute("data-idx-list") || "";
            const markers = idxList
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean)
                .map((item) => `【${item}】`)
                .join("");
            refList.replaceWith(document.createTextNode(markers));
        }
        return clone.innerText.trim();
    }).catch(() => "");
    if (!text || looksLikeLoadingText(text)) {
        return "";
    }
    return normalizeAnswerText(text);
}
async function extractKimiAnswer(page) {
    const locator = page.locator(".chat-content-item-assistant .segment-assistant").last();
    if ((await locator.count().catch(() => 0)) === 0) {
        return "";
    }
    const text = await locator.evaluate((node) => {
        const clone = node.cloneNode(true);
        for (const selector of [
            ".toolcall-container",
            ".segment-assistant-actions",
            ".segment-429-tip",
            ".okc-cards-container"
        ]) {
            clone.querySelectorAll(selector).forEach((element) => element.remove());
        }
        return clone.innerText.trim();
    }).catch(() => "");
    if (!text || looksLikeLoadingText(text)) {
        return "";
    }
    return normalizeAnswerText(text);
}
export async function extractDknowcAnswer(page) {
    if (await isDknowcStillLoading(page)) {
        return "";
    }
    return page.locator(".czkj-robot:not(.chat-load-text) .czkj-msg").evaluateAll((nodes) => {
        // 元素是否真正渲染：checkVisibility/getClientRects 会沿祖先链判断，
        // 只看元素自身 computed style 会漏掉“祖先 display:none”的隐藏面板。
        const isRendered = (element) => {
            if (!element || typeof element !== "object") {
                return true;
            }
            if (element.hidden) {
                return false;
            }
            if (typeof element.checkVisibility === "function") {
                return element.checkVisibility();
            }
            if (typeof element.getClientRects === "function") {
                return element.getClientRects().length > 0;
            }
            return true;
        };
        const texts = nodes
            .filter((node) => !node.closest?.(".czkj-welcome-msg, .zhipu-hot, .czkj-recommend-group"))
            // 隐藏的进度面板（如 .load-deep-search 里的“[检索完成]”）不是回答，不进入候选，
            // 否则正文尚未流出时会回退到它并被当作最终答案。
            .filter((node) => isRendered(node))
            .map((node) => {
            if (typeof node.cloneNode !== "function") {
                return node.innerText?.trim() || "";
            }
            const clone = node.cloneNode(true);
            // 克隆节点脱离文档后 innerText 不再应用 display:none，
            // 必须按原节点（含祖先链）的实际渲染状态剔除隐藏元素（如折叠的推理面板）。
            {
                const liveElements = Array.from(node.querySelectorAll("*"));
                const cloneElements = Array.from(clone.querySelectorAll("*"));
                if (liveElements.length === cloneElements.length) {
                    liveElements.forEach((element, index) => {
                        if (!isRendered(element)) {
                            cloneElements[index].remove();
                        }
                    });
                }
            }
            // 推理面板、思考状态头、知识专库按钮是界面元素，不属于回答正文；展开时同样不采集。
            clone.querySelectorAll(".reasoning_value, .reasoning_tip, .waitText, .chatsse-data.chatSubBtn")
                .forEach((element) => element.remove());
            for (const superscript of Array.from(clone.querySelectorAll("sup"))) {
                const marker = superscript.textContent?.trim() || "";
                if (/^\d{1,4}$/.test(marker)) {
                    superscript.replaceWith(document.createTextNode(`【${marker}】`));
                }
            }
            clone.querySelectorAll(".chat-jb, .chatsse-note-item")
                .forEach((element) => element.remove());
            return clone.innerText?.trim() || "";
        })
            .filter((text) => text
            && !/您好，我是深知晓|很高兴为您服务|您可以试试/.test(text)
            && !/AI正在分析|AI 正在分析|AI 正翻阅|请稍等|正在研究/.test(text));
        return texts.at(-1) || "";
    }).then((text) => {
        const normalized = normalizeAnswerText(text);
        return looksLikeDknowcDeepResearchProgress(normalized) ? "" : normalized;
    }).catch(() => "");
}
async function isDknowcStillLoading(page) {
    const activeGeneration = page.locator(".chat-loading, .stopChat").last();
    if (await activeGeneration.isVisible().catch(() => false)) {
        return true;
    }
    const deepResearchGeneration = page.locator(
        ".czkj-robot.load-deep-search, .czkj-robot .loader-container, .czkj-robot .loader-bar"
    ).last();
    if (await deepResearchGeneration.isVisible().catch(() => false)) {
        return true;
    }
    const loading = page.locator(".czkj-robot.chat-load-text, .chat-load-text, .loading-header").last();
    if (await loading.isVisible().catch(() => false)) {
        const text = await loading.innerText().catch(() => "");
        return !text || looksLikeLoadingText(text) || text.includes("正在研究");
    }
    return false;
}
function looksLikeDknowcDeepResearchProgress(value) {
    const text = String(value || "").replace(/\s+/g, "");
    if (!text) {
        return false;
    }
    if (/^\[(?:查询|检索中|检索完成|思考中|分析中|正在[^\]]*)\]/.test(text) && text.length < 500) {
        return true;
    }
    // 仅由方括号占位符构成的短文本（如“[检索完成]”）是进度状态，不是回答。
    return /^(?:\[[^\]]{1,20}\])+$/.test(text) && text.length < 80;
}
export async function waitForAnswer(config, page, timeoutMs, previousAnswer = "", question = "", control = {}) {
    let deadline = Date.now() + timeoutMs;
    let lastText = "";
    let stableSince = 0;
    let promptReported = false;
    let verificationReported = false;
    let loginReported = false;
    const stableWindowMs = Number(config.completionStableMs)
        || Math.min(10000, Math.max(3000, Math.floor(timeoutMs / 10)));
    while (Date.now() < deadline) {
        if (page.isClosed?.()) {
            throw new Error("采集页面已关闭；将重开页面并自动重放原问题。");
        }
        if (await pageLooksLikeVerification(page)) {
            if (!control.interactive) {
                throw captureStatusError("verification_required", "回答生成期间出现人机验证，当前运行不能代替用户处理。");
            }
            if (!verificationReported) {
                console.log(`${config.label} 回答生成期间出现人工验证。请在当前 Playwright 页面完成后回复“验证已完成”；保持页面打开，采集器会自动续采，无需暂停、取消或重输问题。`);
                verificationReported = true;
            }
            const waitStarted = Date.now();
            const cleared = await waitForVerificationClear(page, control.verificationTimeoutMs || 300000);
            deadline += Date.now() - waitStarted;
            if (!cleared) {
                throw captureStatusError("verification_required", "等待人工验证超时；已保留失败状态，禁止进入后续流程。");
            }
            console.log(`${config.label} 已检测到人工验证完成，继续等待并采集当前回答。`);
            verificationReported = false;
            continue;
        }
        if (config.requiresLogin && await authenticationRequired(page, config)) {
            const recoveredAnswer = await extractAnswer(config, page);
            const hasRecoveredAnswer = recoveredAnswer
                && recoveredAnswer !== previousAnswer
                && !looksLikeQuestionEcho(recoveredAnswer, question)
                && !looksLikeLoginOnlyText(recoveredAnswer)
                && !looksLikeNonAnswerPrompt(recoveredAnswer);
            if (hasRecoveredAnswer) {
                if (recoveredAnswer !== lastText) {
                    lastText = recoveredAnswer;
                    stableSince = Date.now();
                }
                loginReported = false;
            }
            else {
            if (!control.interactive) {
                throw captureStatusError("login_required", "回答生成期间登录态失效，当前运行不能继续采集。");
            }
            if (!loginReported) {
                console.log(`${config.label} 回答生成期间登录态失效。请在当前 Playwright 页面完成登录；保持页面打开，采集器会自动续采，无需重输问题。`);
                loginReported = true;
            }
            const waitStarted = Date.now();
            const authenticated = await waitForAuthentication(page, config, control.verificationTimeoutMs || 300000);
            deadline += Date.now() - waitStarted;
            if (!authenticated) {
                throw captureStatusError("login_required", "等待重新登录超时；已保留失败状态，禁止进入后续流程。");
            }
            console.log(`${config.label} 已检测到登录恢复，继续等待并采集当前回答。`);
            loginReported = false;
            continue;
            }
        }
        const generating = await isGenerationInProgress(config, page);
        if (generating && !lastText) {
            await page.waitForTimeout(1500);
            continue;
        }
        const platformFailure = await detectPlatformFailure(config, page);
        if (platformFailure) {
            throw new Error(platformFailure);
        }
        const text = await extractAnswer(config, page);
        if (previousAnswer && text === previousAnswer) {
            await page.waitForTimeout(1500);
            continue;
        }
        if (question && looksLikeQuestionEcho(text, question)) {
            await page.waitForTimeout(1500);
            continue;
        }
        if (text && looksLikeNonAnswerPrompt(text)) {
            if (!promptReported) {
                console.log(`${config.label} 检测到登录、地区选择或初始化提示；请在浏览器完成处理，采集器会继续等待完整回答。`);
                promptReported = true;
            }
            await page.waitForTimeout(1500);
            continue;
        }
        if (config.name === "yuanbao" && looksLikeYuanbaoInterimAnswer(text)) {
            await page.waitForTimeout(1500);
            continue;
        }
        if (text && text !== lastText) {
            lastText = text;
            stableSince = Date.now();
        }
        else if (text && !generating && stableSince && Date.now() - stableSince >= stableWindowMs) {
            return text;
        }
        await page.waitForTimeout(1500);
    }
    if (lastText) {
        throw new Error(`回答在 ${Math.round(timeoutMs / 1000)} 秒内未确认生成完成，已拒绝保存可能被截断的内容。`);
    }
    return "";
}
async function isGenerationInProgress(config, page) {
    if (isDknowcPlatform(config) && await isDknowcStillLoading(page)) {
        return true;
    }
    const selectors = [
        "#flow-end-msg-stop",
        "button[aria-label*='停止']",
        "button[aria-label*='Stop']",
        "[data-testid*='stop']",
        "[class*='stop-generating']",
        "button:has-text('停止生成')"
    ];
    if (config.name === "yuanbao") {
        selectors.unshift(
            "#yuanbao-send-btn[aria-label='停止回答']",
            "[data-new-input-control='send'] [aria-label='停止回答']",
            ".agent-process-timeline_stepTitleRunning__xgNIa",
            ".hyc-content-loading__text__flash"
        );
    }
    for (const selector of selectors) {
        const locator = page.locator(selector).last();
        if ((await locator.count().catch(() => 0)) > 0 && await locator.isVisible().catch(() => false)) {
            return true;
        }
    }
    return false;
}
async function detectPlatformFailure(config, page) {
    if (config.name !== "kimi") {
        return undefined;
    }
    const bodyText = await page.locator("body").innerText({ timeout: 2000 }).catch(() => "");
    const kimiBusyMarkers = [
        "聊的人太多",
        "Kimi有点累了",
        "高峰期算力不足",
        "请耐心等待",
        "429"
    ];
    if (kimiBusyMarkers.some((marker) => bodyText.includes(marker))) {
        return "Kimi returned a busy/429 response instead of an answer. Please retry later or switch model/account capacity.";
    }
    return undefined;
}
function looksLikeQuestionEcho(text, question) {
    const normalizedText = normalizeComparableText(text);
    const normalizedQuestion = normalizeComparableText(question);
    if (!normalizedText || !normalizedQuestion) {
        return false;
    }
    return normalizedText === normalizedQuestion
        || (normalizedText.length <= normalizedQuestion.length * 1.2
            && (normalizedText.includes(normalizedQuestion) || normalizedQuestion.includes(normalizedText)));
}
function normalizeComparableText(value) {
    return value.replace(/\s+/g, "").trim();
}
async function extractReferences(config, page, question = "") {
    const selectors = config.selectors?.references || ["a[href]"];
    const seen = new Set();
    const references = [];
    let sourceCountAudit;
    if (isDknowcPlatform(config)) {
        references.push(...(await extractDknowcReferences(page, config.url, question)));
        for (const reference of references) {
            seen.add(reference.normalizedUrl);
        }
    }
    if (config.name === "doubao") {
        const diagnostics = {};
        references.push(...(await extractDoubaoReferences(page, config.url, question, diagnostics)));
        sourceCountAudit = diagnostics.sourceCountAudit;
        for (const reference of references) {
            seen.add(reference.normalizedUrl);
        }
    }
    if (config.name === "yuanbao") {
        references.push(...(await extractYuanbaoReferences(page, config.url)));
        for (const reference of references) {
            seen.add(reference.normalizedUrl);
        }
    }
    if (config.name === "deepseek") {
        references.push(...(await extractDeepSeekReferences(page, config.url)));
        for (const reference of references) {
            seen.add(reference.normalizedUrl);
        }
    }
    if (config.name === "qianwen") {
        references.push(...(await extractQianwenReferences(page, config.url)));
        for (const reference of references) {
            seen.add(reference.normalizedUrl);
        }
    }
    for (const selector of selectors) {
        const anchors = await page.locator(selector).evaluateAll((nodes) => nodes
            .map((node) => {
            const element = node;
            const anchor = node;
            const href = anchor.href || element.getAttribute("data-url") || element.getAttribute("href") || "";
            return {
                href,
                text: (element.textContent || "").trim()
            };
        })
            .filter((item) => item.href));
        for (const anchor of anchors) {
            const normalizedUrl = normalizeUrl(anchor.href, config.url);
            if (seen.has(normalizedUrl)) {
                continue;
            }
            seen.add(normalizedUrl);
            references.push({
                title: anchor.text || normalizedUrl,
                url: normalizedUrl,
                normalizedUrl,
                text: anchor.text || undefined
            });
        }
    }
    if (["deepseek", "yuanbao", "qianwen"].includes(config.name)) {
        await hydrateDirectSourceReferences(page, references, config.url);
    }
    return { references, sourceCountAudit };
}
async function extractQianwenReferences(page, baseUrl) {
    const sourceButton = page.locator("text=/\\d+篇来源/").last();
    if (await sourceButton.isVisible().catch(() => false)) {
        await sourceButton.click().catch(async () => {
            await page.locator("[class*='reference-wrap']").last().click().catch(() => undefined);
        });
        await page.waitForTimeout(1200);
    }
    const linked = await page.locator("[role='dialog'] a[href], [data-radix-popper-content-wrapper] a[href], [class*='reference'] a[href], [class*='source'] a[href], [class*='search'] a[href]").evaluateAll((nodes, base) => {
        const seen = new Set();
        const items = [];
        for (const node of nodes) {
            const anchor = node;
            const rawUrl = anchor.href || anchor.getAttribute("href") || "";
            const text = anchor.textContent?.trim().replace(/\s+/g, " ") || "";
            if (!rawUrl || shouldIgnore(rawUrl, text)) {
                continue;
            }
            const normalizedUrl = normalizeInBrowser(rawUrl, base);
            if (seen.has(normalizedUrl)) {
                continue;
            }
            seen.add(normalizedUrl);
            items.push({
                title: text || readableUrlTitle(normalizedUrl),
                url: normalizedUrl,
                normalizedUrl,
                marker: String(items.length + 1),
                text: text || undefined,
                snippet: anchor.closest("li, article, div")?.textContent?.trim().replace(/\s+/g, " ").slice(0, 500) || undefined
            });
        }
        return items;
        function shouldIgnore(rawUrl, text) {
            const lowerUrl = rawUrl.toLowerCase();
            return lowerUrl.includes("alicdn.com")
                || lowerUrl.includes("qianwen.com")
                || lowerUrl.includes("tongyi.aliyun.com")
                || lowerUrl.includes("terms.alicdn.com")
                || rawUrl.startsWith("#")
                || text === "用户协议"
                || text === "隐私政策";
        }
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const url = new URL(rawUrl, baseUrl);
                url.hash = "";
                url.hostname = url.hostname.toLowerCase();
                if (url.pathname !== "/") {
                    url.pathname = url.pathname.replace(/\/+$/, "");
                }
                if (url.pathname === "/") {
                    url.pathname = "";
                }
                return url.toString().replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
        function readableUrlTitle(rawUrl) {
            try {
                const url = new URL(rawUrl);
                const path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
                return path ? `${url.hostname}/${path}` : url.hostname;
            }
            catch {
                return rawUrl;
            }
        }
    }, baseUrl);
    if (linked.length > 0) {
        return linked;
    }
    const sourceCards = await page.locator("[data-c='refer_panel'][data-d='card'], [id^='deep-think-source-card']").evaluateAll((nodes, base) => {
        const seen = new Set();
        const items = [];
        for (const node of nodes) {
            const element = node;
            const rawPayload = element.getAttribute("data-click-extra")
                || element.getAttribute("data-exposure-extra")
                || element.getAttribute("data-log-params")
                || "";
            const payload = parsePayload(rawPayload);
            const rawUrl = payload.ref_url || payload.url || "";
            if (!rawUrl || shouldIgnore(rawUrl)) {
                continue;
            }
            const normalizedUrl = normalizeInBrowser(rawUrl, base);
            const marker = payload.refer_num || element.querySelector("[class*='index']")?.textContent?.trim() || String(items.length + 1);
            const key = `${normalizedUrl}#${marker}`;
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);
            const title = payload.title
                || element.querySelector("[class*='title']")?.textContent?.trim()
                || readableUrlTitle(normalizedUrl);
            const source = element.querySelector("[class*='source'], [class*='name']")?.textContent?.trim().replace(/\s+/g, " ");
            const snippet = element.querySelector("[class*='content']")?.textContent?.trim().replace(/\s+/g, " ").slice(0, 500);
            items.push({
                title,
                url: normalizedUrl,
                normalizedUrl,
                marker,
                text: source || title,
                snippet
            });
        }
        return items;
        function parsePayload(raw) {
            if (!raw) {
                return {};
            }
            try {
                const parsed = JSON.parse(raw);
                return typeof parsed === "object" && parsed ? parsed : {};
            }
            catch {
                return {};
            }
        }
        function shouldIgnore(rawUrl) {
            const lowerUrl = rawUrl.toLowerCase();
            return lowerUrl.includes("alicdn.com")
                || lowerUrl.includes("qianwen.com")
                || lowerUrl.includes("tongyi.aliyun.com")
                || lowerUrl.includes("terms.alicdn.com")
                || rawUrl.startsWith("#");
        }
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const url = new URL(rawUrl, baseUrl);
                url.hash = "";
                url.hostname = url.hostname.toLowerCase();
                if (url.pathname !== "/") {
                    url.pathname = url.pathname.replace(/\/+$/, "");
                }
                if (url.pathname === "/") {
                    url.pathname = "";
                }
                return url.toString().replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
        function readableUrlTitle(rawUrl) {
            try {
                const url = new URL(rawUrl);
                const path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
                return path ? `${url.hostname}/${path}` : url.hostname;
            }
            catch {
                return rawUrl;
            }
        }
    }, baseUrl);
    if (sourceCards.length > 0) {
        return sourceCards;
    }
    return page.locator(".qk-markdown.qk-markdown-react, #qk-markdown-react").last().evaluate((node, base) => {
        const root = node;
        const items = [];
        const text = root.innerText || "";
        const sourceHeading = Array.from(root.querySelectorAll("strong, h3, h2, h4, p, div"))
            .find((element) => /参考文献|政策来源|资料来源|来源/.test(element.textContent || ""));
        const candidates = sourceHeading
            ? Array.from(root.querySelectorAll("ol li, ul li")).filter((element) => {
                const rect = element.getBoundingClientRect();
                const headingRect = sourceHeading.getBoundingClientRect();
                return rect.top >= headingRect.top;
            })
            : [];
        for (const candidate of candidates) {
            const title = candidate.textContent?.trim().replace(/\s+/g, " ");
            if (!title || title.length < 4) {
                continue;
            }
            const marker = String(items.length + 1);
            const url = `${base}#qianwen-source-${marker}`;
            items.push({
                title,
                url,
                normalizedUrl: url,
                marker,
                text: title,
                snippet: title
            });
        }
        if (items.length === 0) {
            const sourceLines = text.split(/\n+/).filter((line) => /《.+》|政策解读|发布(?:日期)?|来源/.test(line));
            for (const line of sourceLines.slice(0, 12)) {
                const marker = String(items.length + 1);
                const url = `${base}#qianwen-source-${marker}`;
                items.push({
                    title: line.trim(),
                    url,
                    normalizedUrl: url,
                    marker,
                    text: line.trim(),
                    snippet: line.trim()
                });
            }
        }
        return items;
    }, baseUrl).catch(() => []);
}
export async function extractDoubaoReferences(page, baseUrl, question = "", diagnostics = {}) {
    const root = page.locator(".md-box-root").last();
    let references = await root.locator("a[href]").evaluateAll((nodes, base) => {
        const seen = new Set();
        const sourceMarkers = new Map();
        const items = [];
        for (const node of nodes) {
            const anchor = node;
            const rawUrl = anchor.href || anchor.getAttribute("href") || "";
            if (!rawUrl) {
                continue;
            }
            const sourceLabel = findSourceLabel(anchor);
            const markerKey = sourceLabel || rawUrl;
            const isFirstMarkerUse = !sourceMarkers.has(markerKey);
            if (isFirstMarkerUse) {
                sourceMarkers.set(markerKey, String(sourceMarkers.size + 1));
            }
            const normalizedUrl = normalizeInBrowser(rawUrl, base);
            if (seen.has(normalizedUrl)) {
                continue;
            }
            seen.add(normalizedUrl);
            const title = anchor.textContent?.trim() || sourceLabel || readableUrlTitle(normalizedUrl);
            items.push({
                title,
                url: normalizedUrl,
                normalizedUrl,
                marker: isFirstMarkerUse ? sourceMarkers.get(markerKey) : undefined,
                text: sourceLabel || title,
                snippet: anchor.closest("li, p, div")?.textContent?.trim().replace(/\s+/g, " ").slice(0, 500) || undefined,
                citationScope: "inline"
            });
        }
        return items;
        function findSourceLabel(anchor) {
            let element = anchor;
            for (let depth = 0; depth < 4 && element; depth += 1) {
                let sibling = element.nextElementSibling;
                while (sibling) {
                    if (/container-(?:DEV3jt|sWvQla)/.test(sibling.className.toString())) {
                        const label = sibling.textContent?.trim().replace(/\s+/g, " ");
                        if (label) {
                            return label;
                        }
                    }
                    sibling = sibling.nextElementSibling;
                }
                const nested = element.querySelector(".container-DEV3jt, .container-sWvQla");
                const label = nested?.textContent?.trim().replace(/\s+/g, " ");
                if (label) {
                    return label;
                }
                element = element.parentElement;
            }
            return undefined;
        }
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const wrapped = new URL(rawUrl, baseUrl);
                const target = wrapped.searchParams.get("target");
                const url = new URL(target || rawUrl, baseUrl);
                url.hash = "";
                url.hostname = url.hostname.toLowerCase();
                if (url.pathname !== "/") {
                    url.pathname = url.pathname.replace(/\/+$/, "");
                }
                if (url.pathname === "/") {
                    url.pathname = "";
                }
                return url.toString().replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
        function readableUrlTitle(rawUrl) {
            try {
                const url = new URL(rawUrl);
                const path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
                return path ? `${url.hostname}/${path}` : url.hostname;
            }
            catch {
                return rawUrl;
            }
        }
    }, baseUrl);
    const searchSources = await extractDoubaoSearchReferences(page, baseUrl);
    for (const reference of searchSources.references) {
        mergeDoubaoReference(references, reference);
    }
    const sourceControls = root.locator(".container-DEV3jt, .container-sWvQla");
    const sourceCount = await sourceControls.count().catch(() => 0);
    const markerByLabel = new Map();
    for (const reference of references) {
        const label = reference.text?.trim();
        if (label && reference.marker && reference.citationScope !== "global") {
            markerByLabel.set(label, reference.marker);
        }
    }
    for (let index = 0; index < sourceCount; index += 1) {
        const source = sourceControls.nth(index);
        if (!(await source.isVisible().catch(() => false))) {
            continue;
        }
        const sourceLabel = (await source.innerText().catch(() => "")).trim().replace(/\s+/g, " ");
        if (!sourceLabel) {
            continue;
        }
        if (!markerByLabel.has(sourceLabel)) {
            markerByLabel.set(sourceLabel, String(markerByLabel.size + 1));
        }
        await source.hover({ timeout: 1500 }).catch(() => undefined);
        await page.waitForTimeout(500);
        const sourceMatchText = sourceLabel.replace(/(?:\.{3}|…)+$/, "").trim() || sourceLabel;
        const visiblePopovers = page.locator(".semi-popover-wrapper-show:visible");
        let popover = visiblePopovers.filter({ hasText: sourceMatchText }).last();
        let popoverVisible = await popover.isVisible().catch(() => false);
        if (!popoverVisible && await visiblePopovers.count().catch(() => 0) > 0) {
            popover = visiblePopovers.last();
            popoverVisible = await popover.isVisible().catch(() => false);
        }
        if (!popoverVisible) {
            await source.click({ timeout: 1500 }).catch(() => undefined);
            await page.waitForTimeout(350);
            const clickedPopovers = page.locator(".semi-popover-wrapper-show:visible");
            popover = clickedPopovers.filter({ hasText: sourceMatchText }).last();
            popoverVisible = await popover.isVisible().catch(() => false);
            if (!popoverVisible && await clickedPopovers.count().catch(() => 0) > 0) {
                popover = clickedPopovers.last();
                popoverVisible = await popover.isVisible().catch(() => false);
            }
        }
        if (!popoverVisible) {
            continue;
        }
        const embeddedReferences = await popover
            .locator("a[href], [data-url], [data-href], [data-target], [data-link]")
            .evaluateAll((nodes, base) => {
            const items = [];
            for (const node of nodes) {
                const element = node;
                const rawUrl = element.href
                    || element.getAttribute("href")
                    || element.getAttribute("data-url")
                    || element.getAttribute("data-href")
                    || element.getAttribute("data-target")
                    || element.getAttribute("data-link")
                    || "";
                const text = element.textContent?.trim().replace(/\s+/g, " ") || "";
                if (!rawUrl) {
                    continue;
                }
                const normalizedUrl = normalizeInBrowser(rawUrl, base);
                if (shouldIgnore(normalizedUrl, text)) {
                    continue;
                }
                items.push({
                    title: text || readableUrlTitle(normalizedUrl),
                    url: normalizedUrl,
                    normalizedUrl,
                    text: text || undefined,
                    snippet: element.closest("li, article, div")?.textContent?.trim().replace(/\s+/g, " ").slice(0, 500) || undefined
                });
            }
            return items;
            function shouldIgnore(rawUrl, text) {
                const lowerUrl = rawUrl.toLowerCase();
                return lowerUrl.includes("doubao.com/chat")
                    || lowerUrl.includes("byteimg.com")
                    || lowerUrl.includes("flow-web-cdn")
                    || rawUrl.startsWith("#")
                    || text === "用户协议"
                    || text === "隐私政策";
            }
            function normalizeInBrowser(rawUrl, baseUrl) {
                try {
                    const wrapped = new URL(rawUrl, baseUrl);
                    const target = wrapped.searchParams.get("target");
                    const url = new URL(target || rawUrl, baseUrl);
                    url.hash = "";
                    url.hostname = url.hostname.toLowerCase();
                    if (url.pathname !== "/") {
                        url.pathname = url.pathname.replace(/\/+$/, "");
                    }
                    if (url.pathname === "/") {
                        url.pathname = "";
                    }
                    return url.toString().replace(/\/$/, "");
                }
                catch {
                    return rawUrl.trim();
                }
            }
            function readableUrlTitle(rawUrl) {
                try {
                    const url = new URL(rawUrl);
                    const path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
                    return path ? `${url.hostname}/${path}` : url.hostname;
                }
                catch {
                    return rawUrl;
                }
            }
        }, baseUrl).catch(() => []);
        for (const reference of embeddedReferences) {
            mergeDoubaoReference(references, {
                ...reference,
                marker: markerByLabel.get(sourceLabel),
                text: sourceLabel,
                citationScope: "inline"
            });
        }
        if (embeddedReferences.length > 0) {
            continue;
        }
        const card = popover.locator(".content-ir5YyT, .title-ehsWfR").first();
        if (!(await card.isVisible().catch(() => false))) {
            continue;
        }
        const cardTitle = (await popover.locator(".title-ehsWfR").first().innerText().catch(() => sourceLabel))
            .trim()
            .replace(/\s+/g, " ");
        const destinationUrl = await clickDoubaoSourceCard(page, card);
        if (!destinationUrl) {
            continue;
        }
        const normalizedUrl = normalizeUrl(destinationUrl, baseUrl);
        const effectiveTitle = cardTitle && !/^untitled$/i.test(cardTitle) ? cardTitle : sourceLabel;
        if (shouldIgnoreDoubaoReference(normalizedUrl, effectiveTitle)) {
            continue;
        }
        mergeDoubaoReference(references, {
            title: effectiveTitle,
            url: normalizedUrl,
            normalizedUrl,
            marker: markerByLabel.get(sourceLabel),
            text: sourceLabel,
            snippet: effectiveTitle,
            citationScope: "inline"
        });
    }
    references = normalizeAndMergeDoubaoReferences(references, baseUrl);
    if (searchSources.expectedCount > 0 && searchSources.references.length === 0) {
        throw captureStatusError(
            "failed",
            `豆包页面声明参考 ${searchSources.expectedCount} 篇资料，但来源入口未展开出任何可审计来源；必须重新采集或由 Computer Use 接管。`,
            { partialReferences: references }
        );
    }
    await hydrateDoubaoReferenceContent(page, references, question);
    const incompleteInlinePdfs = references.filter((reference) => isPdfReference(reference.url)
        && ["inline", "inline_and_global"].includes(reference.citationScope)
        && !hasSubstantiveReferenceContent(reference));
    if (incompleteInlinePdfs.length > 0) {
        throw captureStatusError(
            "failed",
            `豆包有 ${incompleteInlinePdfs.length} 个已绑定 PDF 来源未取得可核验正文；必须继续正文提取、OCR 或由 Computer Use 接管，禁止把采集缺口判成平台幻觉。`,
            { partialReferences: references }
        );
    }
    const capturedSources = references.filter((reference) => [
        "inline", "global", "inline_and_global"
    ].includes(reference.citationScope)).length;
    if (searchSources.expectedCount > 0) {
        const countDiffers = searchSources.expectedCount !== capturedSources;
        diagnostics.sourceCountAudit = {
            platformDeclaredCount: searchSources.expectedCount,
            auditableReferenceCount: capturedSources,
            status: countDiffers ? "declared_count_differs" : "matched",
            note: countDiffers
                ? `豆包页面声明参考 ${searchSources.expectedCount} 篇资料，实际提供 ${capturedSources} 条可审计来源；已按页面实际可访问来源完成采集。`
                : `豆包页面声明并实际采集 ${capturedSources} 条可审计来源。`
        };
    }
    return references;
}
async function extractDoubaoSearchReferences(page, baseUrl) {
    const summary = page.locator("text=/搜索\\s*\\d+\\s*个关键词，参考\\s*\\d+\\s*篇资料/").last();
    if (!(await summary.isVisible().catch(() => false))) {
        return { expectedCount: 0, references: [] };
    }
    const summaryText = (await summary.innerText().catch(() => "")).trim().replace(/\s+/g, " ");
    const expectedCount = Number(summaryText.match(/参考\s*(\d+)\s*篇资料/)?.[1] || 0);
    const beforeVisibleUrls = await collectVisibleExternalUrls(page, baseUrl);
    const clickTarget = summary.locator("xpath=..");
    const clicked = await clickTarget.click({ timeout: 2500 }).then(() => true).catch(() => false);
    if (!clicked) {
        await summary.click({ timeout: 2500 }).catch(() => undefined);
    }
    await page.waitForTimeout(700);
    const references = [];
    for (let round = 0; round < 10; round += 1) {
        const candidates = await collectDoubaoVisibleSourceCandidates(page, baseUrl, beforeVisibleUrls);
        for (const candidate of candidates) {
            mergeDoubaoReference(references, candidate);
        }
        if (expectedCount > 0 && references.length >= expectedCount) {
            break;
        }
        const scrolled = await scrollDoubaoSourceSurfaces(page);
        if (!scrolled && round >= 1) {
            break;
        }
        await page.waitForTimeout(250);
    }
    await page.keyboard.press("Escape").catch(() => undefined);
    return { expectedCount, references };
}
async function collectVisibleExternalUrls(page, baseUrl) {
    return page.locator("a[href]:visible, [data-url]:visible, [data-href]:visible, [data-target]:visible, [data-link]:visible")
        .evaluateAll((nodes, base) => {
        const urls = new Set();
        for (const node of nodes) {
            const element = node;
            const rawUrl = element.href
                || element.getAttribute("href")
                || element.getAttribute("data-url")
                || element.getAttribute("data-href")
                || element.getAttribute("data-target")
                || element.getAttribute("data-link")
                || "";
            if (!rawUrl) {
                continue;
            }
            const normalizedUrl = normalizeInBrowser(rawUrl, base);
            if (!shouldIgnore(normalizedUrl)) {
                urls.add(normalizedUrl);
            }
        }
        return [...urls];
        function shouldIgnore(rawUrl) {
            const lowerUrl = rawUrl.toLowerCase();
            return lowerUrl.includes("doubao.com")
                || lowerUrl.includes("byteimg.com")
                || lowerUrl.includes("flow-web-cdn")
                || lowerUrl.includes("bytednsdoc.com")
                || rawUrl.startsWith("#");
        }
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const wrapped = new URL(rawUrl, baseUrl);
                const target = wrapped.searchParams.get("target");
                const url = new URL(target || rawUrl, baseUrl);
                url.hash = "";
                url.hostname = url.hostname.toLowerCase();
                return url.toString().replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
    }, baseUrl).catch(() => []);
}
async function collectDoubaoVisibleSourceCandidates(page, baseUrl, beforeVisibleUrls) {
    return page.locator("a[href]:visible, [data-url]:visible, [data-href]:visible, [data-target]:visible, [data-link]:visible")
        .evaluateAll((nodes, payload) => {
        const before = new Set(payload.before);
        const seen = new Set();
        const items = [];
        for (const node of nodes) {
            const element = node;
            const rawUrl = element.href
                || element.getAttribute("href")
                || element.getAttribute("data-url")
                || element.getAttribute("data-href")
                || element.getAttribute("data-target")
                || element.getAttribute("data-link")
                || "";
            if (!rawUrl) {
                continue;
            }
            const normalizedUrl = normalizeInBrowser(rawUrl, payload.base);
            if (shouldIgnore(normalizedUrl) || seen.has(normalizedUrl)) {
                continue;
            }
            const sourceSurface = element.closest("[role='dialog'], [class*='drawer'], [class*='modal'], [class*='sheet'], [class*='popover'], [class*='reference'], [class*='source'], [class*='search']");
            if (!sourceSurface && before.has(normalizedUrl)) {
                continue;
            }
            const container = element.closest("article, li, [class*='card'], [class*='item'], [class*='content']") || element.parentElement || element;
            const containerText = container.textContent?.trim().replace(/\s+/g, " ") || "";
            const elementText = element.textContent?.trim().replace(/\s+/g, " ") || "";
            const heading = container.querySelector("h1, h2, h3, h4, [class*='title']");
            const title = heading?.textContent?.trim().replace(/\s+/g, " ")
                || elementText
                || readableUrlTitle(normalizedUrl);
            seen.add(normalizedUrl);
            items.push({
                title,
                url: normalizedUrl,
                normalizedUrl,
                text: elementText || title,
                snippet: containerText && containerText !== title ? containerText.slice(0, 2000) : title,
                citationScope: "global"
            });
        }
        return items;
        function shouldIgnore(rawUrl) {
            const lowerUrl = rawUrl.toLowerCase();
            return lowerUrl.includes("doubao.com")
                || lowerUrl.includes("byteimg.com")
                || lowerUrl.includes("flow-web-cdn")
                || lowerUrl.includes("bytednsdoc.com")
                || rawUrl.startsWith("#");
        }
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const wrapped = new URL(rawUrl, baseUrl);
                const target = wrapped.searchParams.get("target");
                const url = new URL(target || rawUrl, baseUrl);
                url.hash = "";
                url.hostname = url.hostname.toLowerCase();
                if (url.pathname !== "/") {
                    url.pathname = url.pathname.replace(/\/+$/, "");
                }
                if (url.pathname === "/") {
                    url.pathname = "";
                }
                return url.toString().replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
        function readableUrlTitle(rawUrl) {
            try {
                const url = new URL(rawUrl);
                const path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
                return path ? `${url.hostname}/${path}` : url.hostname;
            }
            catch {
                return rawUrl;
            }
        }
    }, { base: baseUrl, before: beforeVisibleUrls }).catch(() => []);
}
async function scrollDoubaoSourceSurfaces(page) {
    return page.locator("[role='dialog']:visible, [class*='drawer']:visible, [class*='modal']:visible, [class*='sheet']:visible, [class*='reference']:visible, [class*='source']:visible, [class*='search']:visible")
        .evaluateAll((nodes) => {
        let scrolled = false;
        for (const root of nodes) {
            const candidates = [root, ...root.querySelectorAll("div, ul, ol")];
            for (const candidate of candidates) {
                if (candidate.scrollHeight <= candidate.clientHeight + 8) {
                    continue;
                }
                const before = candidate.scrollTop;
                candidate.scrollTop = Math.min(candidate.scrollHeight, candidate.scrollTop + Math.max(300, candidate.clientHeight * 0.8));
                scrolled = scrolled || candidate.scrollTop > before;
            }
        }
        return scrolled;
    }).catch(() => false);
}
function mergeDoubaoReference(references, incoming) {
    const existing = references.find((reference) => reference.normalizedUrl === incoming.normalizedUrl);
    if (!existing) {
        references.push(incoming);
        return;
    }
    existing.title = longerText(existing.title, incoming.title);
    existing.text = longerText(existing.text, incoming.text);
    existing.snippet = longerText(existing.snippet, incoming.snippet);
    existing.content = longerText(existing.content, incoming.content);
    existing.marker = existing.marker || incoming.marker;
    existing.citationScope = combineCitationScope(existing.citationScope, incoming.citationScope);
}
function longerText(first, second) {
    if (!first) {
        return second;
    }
    if (!second) {
        return first;
    }
    return second.length > first.length ? second : first;
}
function combineCitationScope(first, second) {
    if (!first) {
        return second;
    }
    if (!second || first === second) {
        return first;
    }
    return "inline_and_global";
}
function normalizeAndMergeDoubaoReferences(references, baseUrl) {
    const merged = [];
    for (const reference of references) {
        const cleanedUrl = normalizeUrl(reference.url || reference.normalizedUrl || "", baseUrl);
        mergeDoubaoReference(merged, {
            ...reference,
            url: cleanedUrl,
            normalizedUrl: cleanedUrl
        });
    }
    return merged;
}
async function hydrateDoubaoReferenceContent(page, references, question = "") {
    const queue = references.filter((reference) => isPdfReference(reference.url)
        ? !hasSubstantiveReferenceContent(reference)
        : shouldHydrateDoubaoReference(reference));
    if (queue.length === 0) {
        return;
    }
    let cursor = 0;
    const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
        while (cursor < queue.length) {
            const reference = queue[cursor];
            cursor += 1;
            const trustedMatch = await extractTrustedSearchReference(reference, question);
            if (trustedMatch?.content) {
                reference.snippet = longerText(reference.snippet, trustedMatch.content.slice(0, 2000));
                reference.content = longerText(reference.content, trustedMatch.content);
                reference.contentAcquisition = "trusted_search_full_content";
                continue;
            }
            if (isPdfReference(reference.url)) {
                const pdfContent = await extractPdfReferenceContent(page, reference.url);
                if (pdfContent) {
                    reference.snippet = longerText(reference.snippet, pdfContent.slice(0, 2000));
                    reference.content = longerText(reference.content, pdfContent);
                    reference.contentAcquisition = "direct_pdf_extraction";
                }
                continue;
            }
            const sourcePage = await page.context().newPage().catch(() => undefined);
            if (!sourcePage) {
                continue;
            }
            try {
                sourcePage.setDefaultTimeout(12000);
                sourcePage.setDefaultNavigationTimeout(12000);
                await sourcePage.goto(reference.url, {
                    waitUntil: "domcontentloaded",
                    timeout: 12000
                });
                await sourcePage.waitForTimeout(500);
                const extracted = await extractSourcePageContent(sourcePage);
                if (!extracted.content) {
                    continue;
                }
                reference.title = longerText(reference.title, extracted.title);
                reference.snippet = longerText(reference.snippet, extracted.content.slice(0, 2000));
                reference.content = longerText(reference.content, extracted.content);
                reference.contentAcquisition = "direct_page_extraction";
            }
            catch {
                // The original URL remains valid evidence when the source blocks automated reading.
            }
            finally {
                await sourcePage.close().catch(() => undefined);
            }
        }
    });
    await Promise.all(workers);
}

export async function hydrateDirectSourceReferences(page, references, platformUrl) {
    const queue = references.filter((reference) => /^https?:\/\//i.test(reference.url || ""));
    let cursor = 0;
    const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
        while (cursor < queue.length) {
            const reference = queue[cursor];
            cursor += 1;
            if (normalizeUrl(reference.url, platformUrl) === normalizeUrl(platformUrl)) {
                reference.sourceAcquisitionStatus = "failed";
                reference.sourceAcquisitionError = "引用卡未暴露可访问的外部来源 URL";
                continue;
            }
            if (isPdfReference(reference.url)) {
                const pdfContent = await extractPdfReferenceContent(page, reference.url);
                if (pdfContent) {
                    reference.snippet = pdfContent.slice(0, 2000);
                    reference.snippetProvenance = "source_document";
                    reference.content = pdfContent;
                    reference.contentAcquisition = "direct_pdf_extraction";
                    reference.sourceAcquisitionStatus = "captured";
                }
                else {
                    reference.sourceAcquisitionStatus = "failed";
                    reference.sourceAcquisitionError = "PDF 正文提取未完成";
                }
                continue;
            }
            const sourcePage = await page.context().newPage().catch(() => undefined);
            if (!sourcePage) {
                reference.sourceAcquisitionStatus = "failed";
                reference.sourceAcquisitionError = "无法创建来源页面";
                continue;
            }
            try {
                sourcePage.setDefaultTimeout(15000);
                sourcePage.setDefaultNavigationTimeout(15000);
                const response = await sourcePage.goto(reference.url, {
                    waitUntil: "domcontentloaded",
                    timeout: 15000
                });
                await sourcePage.waitForTimeout(500);
                reference.sourceResolvedUrl = sourcePage.url();
                const status = response?.status() || 0;
                if ([401, 403, 429].includes(status)) {
                    reference.sourceAcquisitionStatus = "blocked";
                    reference.sourceAcquisitionError = `来源页面返回 HTTP ${status}`;
                    continue;
                }
                const extracted = await extractSourcePageContent(sourcePage);
                if (extracted.blocked) {
                    reference.sourceAcquisitionStatus = "blocked";
                    reference.sourceAcquisitionError = extracted.reason || "来源页面要求人机验证或拒绝访问";
                    continue;
                }
                if (!extracted.content) {
                    reference.sourceAcquisitionStatus = "failed";
                    reference.sourceAcquisitionError = "来源页面未提取到可核验正文";
                    continue;
                }
                reference.title = longerText(reference.title, extracted.title);
                reference.snippet = extracted.content.slice(0, 2000);
                reference.snippetProvenance = "source_document";
                reference.content = extracted.content;
                reference.contentAcquisition = "direct_page_extraction";
                reference.sourceAcquisitionStatus = "captured";
                delete reference.sourceAcquisitionError;
            }
            catch (error) {
                reference.sourceAcquisitionStatus = "failed";
                reference.sourceAcquisitionError = error instanceof Error
                    ? error.message.slice(0, 300)
                    : String(error).slice(0, 300);
            }
            finally {
                await sourcePage.close().catch(() => undefined);
            }
        }
    });
    await Promise.all(workers);
}
async function extractTrustedSearchReference(reference, question = "") {
    const key = await trustedSearchKey();
    if (!key) {
        return undefined;
    }
    const endpoint = String(process.env.FACTCHECK_TRUSTED_SEARCH_URL || "https://open.dknowc.cn/dependable/search").trim();
    const expectation = inferOriginExpectation(reference);
    const query = [String(reference.title || "").trim(), expectation.publisherQuery]
        .filter(Boolean)
        .join(" ")
        .slice(0, 500);
    if (!query) {
        return undefined;
    }
    const cacheKey = `${endpoint}\n${normalizeUrl(reference.url || "")}\n${query}`;
    if (trustedSearchContentCache.has(cacheKey)) {
        return trustedSearchContentCache.get(cacheKey);
    }
    try {
        const response = await fetch(endpoint, {
            method: "POST",
            redirect: "follow",
            signal: AbortSignal.timeout(35000),
            headers: {
                "api-key": key,
                "content-type": "application/json"
            },
            body: JSON.stringify({
                query,
                return_full_content: true,
                segmentCount: 10,
                simplified: false,
                ...(expectation.serviceArea ? { service_area: expectation.serviceArea } : {})
            })
        });
        if (!response.ok) {
            return undefined;
        }
        const payload = await response.json();
        const articles = payload?.content?.data?.["检索文章"] || [];
        const referenceUrl = normalizeUrl(reference.url || "");
        const referenceTitle = normalizeDocumentTitle(String(reference.title || ""));
        const ranked = articles
            .map((article) => {
            const articleUrl = normalizeUrl(String(article?.["源网址"] || ""));
            const articleTitle = normalizeDocumentTitle(String(article?.["文章标题"] || ""));
            const publisher = String(article?.["发布或实施机构"] || "").trim();
            const region = String(article?.["办理地域"] || article?.["地域"] || "").trim();
            const dataSource = String(article?.["数据源"] || "").trim();
            let score = 0;
            if (referenceUrl && articleUrl === referenceUrl) {
                score += 100;
            }
            if (referenceTitle && articleTitle && referenceTitle === articleTitle) {
                score += 80;
            }
            const full = String(article?.["全文"] || "").trim();
            const segments = (article?.["段落"] || [])
                .map((segment) => String(segment?.["内容"] || "").trim())
                .filter(Boolean)
                .join("\n");
            const body = full && segments && !full.includes(segments)
                ? `${segments}\n${full}`
                : (full || segments);
            const exactTitle = Boolean(referenceTitle && articleTitle && referenceTitle === articleTitle);
            const excerptAligned = strongExcerptOverlap(reference, body);
            const publisherAligned = matchesExpectedPublisher(publisher, expectation);
            const sameMaterialVerified = exactTitle
                && Boolean(body)
                && (excerptAligned || publisherAligned || (referenceUrl && articleUrl === referenceUrl));
            if (excerptAligned) {
                score += 60;
            }
            if (publisherAligned) {
                score += 35;
            }
            if (articleUrl) {
                score += 120;
            }
            return {
                score,
                body,
                url: articleUrl,
                title: String(article?.["文章标题"] || "").trim(),
                publisher,
                region,
                dataSource,
                sameMaterialVerified,
                officialSourceUrl: Boolean(articleUrl)
            };
        })
            .filter((item) => item.sameMaterialVerified)
            .sort((first, second) => second.score - first.score);
        const best = ranked[0];
        const content = String(best?.body || "")
            .replace(/\s+/g, " ")
            .replace(/([\u3400-\u9fff])\s+(?=[\u3400-\u9fff])/g, "$1")
            .trim()
            .slice(0, 12000);
        if (!content) {
            return undefined;
        }
        const matched = {
            content,
            url: String(best?.url || ""),
            title: String(best?.title || ""),
            publisher: String(best?.publisher || ""),
            region: String(best?.region || ""),
            dataSource: String(best?.dataSource || ""),
            sameMaterialVerified: Boolean(best?.sameMaterialVerified),
            officialSourceUrl: Boolean(best?.officialSourceUrl)
        };
        trustedSearchContentCache.set(cacheKey, matched);
        return matched;
    }
    catch {
        return undefined;
    }
}
export async function hydrateDknowcReferenceContent(references, question = "") {
    for (const reference of references) {
        reference.platformTrustSource = "dknow_reference_capture";
    }
    const queue = references.filter((reference) => isDknowInternalUrl(reference.url));
    if (!(await trustedSearchKey())) {
        return;
    }
    let cursor = 0;
    const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
        while (cursor < queue.length) {
            const reference = queue[cursor];
            cursor += 1;
            const trustedMatch = await extractTrustedSearchReference(reference, question);
            if (!trustedMatch?.content) {
                reference.originAttributionStatus = "trusted_search_no_source_url";
                reference.originAttributionReason = "可信搜索未返回同一材料的源网址，保留深知收录页作为兜底。";
                continue;
            }
            // Keep the platform URL byte-for-byte because 深知 uses its hash route
            // (including the policyDetails id) as the durable collection-page locator.
            const capturedUrl = String(reference.url || reference.normalizedUrl || "").trim();
            const officialUrl = normalizeUrl(trustedMatch.url || "");
            reference.title = longerText(reference.title, trustedMatch.title);
            reference.snippet = longerText(reference.snippet, trustedMatch.content.slice(0, 2000));
            reference.content = longerText(reference.content, trustedMatch.content);
            reference.contentAcquisition = "trusted_search_full_content";
            reference.sameMaterialVerified = Boolean(trustedMatch.sameMaterialVerified);
            reference.trustedSearchCandidateUrl = officialUrl || undefined;
            reference.trustedSearchPublisher = trustedMatch.publisher || undefined;
            reference.trustedSearchRegion = trustedMatch.region || undefined;
            reference.trustedSearchDataSource = trustedMatch.dataSource || undefined;
            if (officialUrl && !isDknowInternalUrl(officialUrl) && trustedMatch.officialSourceUrl) {
                reference.platformUrl = capturedUrl;
                reference.originalUrl = capturedUrl;
                reference.officialUrl = officialUrl;
                reference.resourceUrl = officialUrl;
                reference.sourceUrl = officialUrl;
                reference.url = officialUrl;
                reference.normalizedUrl = officialUrl;
                reference.originAttributionStatus = "trusted_search_official_url";
                reference.originAttributionReason = "可信搜索返回的源网址，按已确认产品口径作为官方来源主链接。";
            }
            else {
                reference.originAttributionStatus = "trusted_search_no_source_url";
                reference.originAttributionReason = "可信搜索全文可直接用于判断，但未返回可用源网址，保留深知收录页作为兜底。";
            }
        }
    });
    await Promise.all(workers);
}

function normalizeDocumentTitle(value) {
    return String(value || "")
        .replace(/^(?:[【\[]\s*(?:\d{4}|已结束|有效|现行|失效|废止)\s*[】\]])+/g, "")
        .replace(/[《》“”"'‘’\s\u3000·•—–\-_:：，,。.!！?？()（）【】\[\]]/g, "")
        .trim();
}

function compactEvidenceText(value) {
    return String(value || "")
        .replace(/<[^>]+>/g, "")
        .replace(/[《》“”"'‘’\s\u3000·•—–\-_:：，,。.!！?？()（）【】\[\]；;、]/g, "")
        .trim();
}

function strongExcerptOverlap(reference, body) {
    const evidence = compactEvidenceText(reference.snippet || reference.content || reference.text || "");
    const candidate = compactEvidenceText(body);
    if (evidence.length < 30 || candidate.length < 30) {
        return false;
    }
    const windows = [];
    for (let index = 0; index + 30 <= evidence.length && windows.length < 8; index += 20) {
        windows.push(evidence.slice(index, index + Math.min(60, evidence.length - index)));
    }
    return windows.some((window) => window.length >= 30 && candidate.includes(window));
}

function inferOriginExpectation(reference) {
    const title = String(reference.title || "");
    const snippet = String(reference.snippet || reference.content || "");
    const combined = `${title}\n${snippet}`;
    const definitions = [
        {
            test: /北京市教育委员会/,
            publishers: ["北京市教育委员会"],
            hosts: ["jw.beijing.gov.cn"],
            publisherQuery: "北京市教育委员会",
            serviceArea: "北京市"
        },
        {
            test: /北京市人力资源和社会保障局/,
            publishers: ["北京市人力资源和社会保障局"],
            hosts: ["rsj.beijing.gov.cn"],
            publisherQuery: "北京市人力资源和社会保障局",
            serviceArea: "北京市"
        },
        {
            test: /北京市市场监督管理局|北京市市场监管局/,
            publishers: ["北京市市场监督管理局", "北京市市场监管局"],
            hosts: ["scjgj.beijing.gov.cn"],
            publisherQuery: "北京市市场监督管理局",
            serviceArea: "北京市"
        },
        {
            test: /中共北京市委办公厅|北京市人民政府办公厅/,
            publishers: ["中共北京市委办公厅", "北京市人民政府办公厅"],
            hosts: ["beijing.gov.cn"],
            publisherQuery: "北京市人民政府",
            serviceArea: "北京市"
        },
        {
            test: /中共中央办公厅|国务院办公厅/,
            publishers: ["中共中央办公厅", "国务院办公厅"],
            hosts: ["gov.cn"],
            publisherQuery: "国务院",
            serviceArea: ""
        },
        {
            test: /教育部办公厅|中华人民共和国教育部令|教育部关于|校外培训行政处罚暂行办法/,
            publishers: ["教育部", "教育部办公厅"],
            hosts: ["moe.gov.cn"],
            publisherQuery: "教育部",
            serviceArea: ""
        }
    ];
    const match = definitions.find((definition) => definition.test.test(title))
        || definitions.find((definition) => definition.test.test(combined));
    return match || {
        publishers: [],
        hosts: [],
        publisherQuery: "",
        serviceArea: ""
    };
}

function matchesExpectedPublisher(publisher, expectation) {
    const normalizedPublisher = normalizeComparableText(String(publisher || ""));
    return Boolean(normalizedPublisher && expectation.publishers.some((candidate) => {
        const normalizedCandidate = normalizeComparableText(candidate);
        return normalizedPublisher.includes(normalizedCandidate)
            || normalizedCandidate.includes(normalizedPublisher);
    }));
}

function isDknowInternalUrl(rawUrl) {
    try {
        const url = new URL(String(rawUrl || ""));
        return url.hostname.toLowerCase().includes("dknowc.cn")
            || url.pathname.toUpperCase().includes("/DT_DATA/");
    }
    catch {
        return String(rawUrl || "").toUpperCase().includes("/DT_DATA/");
    }
}
export function isPdfReference(rawUrl) {
    try {
        return new URL(rawUrl).pathname.toLowerCase().endsWith(".pdf");
    }
    catch {
        return /\.pdf(?:$|[?#])/i.test(String(rawUrl || ""));
    }
}
function hasSubstantiveReferenceContent(reference) {
    const title = String(reference.title || "").trim();
    const text = String(reference.text || "").trim();
    const snippet = String(reference.snippet || "").trim();
    const content = String(reference.content || "").trim();
    if (content.length >= 80) {
        return true;
    }
    return snippet.length >= 120 && snippet !== title && snippet !== text;
}
export async function extractPdfReferenceContent(page, rawUrl) {
    let temporaryDirectory;
    try {
        const response = await fetch(rawUrl, {
            redirect: "follow",
            signal: AbortSignal.timeout(240000),
            headers: {
                "user-agent": "Mozilla/5.0 Fact-Check-X source verifier"
            }
        });
        if (!response.ok) {
            return "";
        }
        const contentLength = Number(response.headers.get("content-length") || 0);
        if (contentLength > 25 * 1024 * 1024) {
            return "";
        }
        const buffer = Buffer.from(await response.arrayBuffer());
        if (buffer.length > 25 * 1024 * 1024 || !buffer.subarray(0, 5).equals(Buffer.from("%PDF-"))) {
            return "";
        }
        temporaryDirectory = await mkdtemp(join(tmpdir(), "fact-check-x-pdf-"));
        const inputPath = join(temporaryDirectory, "source.pdf");
        const outputPath = join(temporaryDirectory, "source.txt");
        await writeFile(inputPath, buffer);
        await execFileAsync("pdftotext", ["-layout", inputPath, outputPath], {
            timeout: 90000,
            maxBuffer: 2 * 1024 * 1024
        });
        const extracted = (await readFile(outputPath, "utf8"))
            .replace(/\s+/g, " ")
            .replace(/([\u3400-\u9fff])\s+(?=[\u3400-\u9fff])/g, "$1")
            .trim();
        return extracted.slice(0, 12000);
    }
    catch (error) {
        console.warn(`PDF 正文提取未完成：${error instanceof Error ? error.message : String(error)}`);
        return "";
    }
    finally {
        if (temporaryDirectory) {
            await rm(temporaryDirectory, { recursive: true, force: true }).catch(() => undefined);
        }
    }
}
function shouldHydrateDoubaoReference(reference) {
    if (!/^https?:\/\//i.test(reference.url || "") || shouldIgnoreDoubaoReference(reference.url, reference.title || "")) {
        return false;
    }
    const content = String(reference.content || "").trim();
    if (content.length >= 120) {
        return false;
    }
    const snippet = String(reference.snippet || "").trim();
    const labels = new Set([
        String(reference.title || "").trim(),
        String(reference.text || "").trim()
    ]);
    return snippet.length < 120 || labels.has(snippet);
}
async function extractSourcePageContent(sourcePage) {
    return sourcePage.evaluate(() => {
        const clean = (value) => String(value || "").trim().replace(/\s+/g, " ");
        const selectors = [
            "article",
            "main",
            "[role='main']",
            "#content",
            "#article-content",
            ".article-content",
            ".article_detail",
            ".content"
        ];
        const candidates = [];
        for (const selector of selectors) {
            for (const element of document.querySelectorAll(selector)) {
                const text = clean(element.innerText || element.textContent);
                if (text.length >= 40) {
                    candidates.push(text);
                }
            }
        }
        const bodyText = clean(document.body?.innerText || document.body?.textContent);
        if (bodyText.length >= 40) {
            candidates.push(bodyText);
        }
        candidates.sort((first, second) => second.length - first.length);
        const content = candidates[0] || "";
        const blocked = /(?:访问过于频繁|安全验证|人机验证|请输入验证码|access denied|forbidden)/i.test(content);
        return {
            title: clean(document.title),
            content: blocked ? "" : content.slice(0, 12000),
            blocked,
            reason: blocked ? "来源页面要求人机验证或拒绝访问" : ""
        };
    }).catch(() => ({ title: "", content: "", blocked: false, reason: "" }));
}
async function clickDoubaoSourceCard(page, card) {
    const beforeUrl = page.url();
    const context = page.context();
    await page.evaluate(() => {
        const probeKey = "__factCheckWindowOpenProbe";
        const existing = window[probeKey];
        if (existing?.originalOpen) {
            window.open = existing.originalOpen;
        }
        const originalOpen = window.open;
        window[probeKey] = {
            originalOpen,
            url: ""
        };
        window.open = function (...args) {
            const target = args[0];
            window[probeKey].url = typeof target === "string"
                ? target
                : String(target || "");
            return Reflect.apply(originalOpen, this, args);
        };
    }).catch(() => undefined);
    const destinationPromise = Promise.race([
        context.waitForEvent("page", { timeout: 12000 })
            .then((popup) => ({ type: "popup", popup }))
            .catch(() => undefined),
        page.waitForURL((url) => url.toString() !== beforeUrl, { timeout: 12000 })
            .then(() => ({ type: "navigation" }))
            .catch(() => undefined),
        page.waitForTimeout(12500).then(() => undefined)
    ]);
    const clicked = await card.click({ timeout: 8000 }).then(() => true).catch(() => false);
    if (!clicked) {
        await restoreDoubaoWindowOpen(page);
        return "";
    }
    const destination = await destinationPromise;
    const probedUrl = await restoreDoubaoWindowOpen(page);
    if (destination?.type === "popup") {
        const popup = destination.popup;
        await popup.waitForLoadState("domcontentloaded", { timeout: 12000 }).catch(() => undefined);
        await popup.waitForTimeout(500).catch(() => undefined);
        const url = popup.url();
        await popup.close().catch(() => undefined);
        return url === "about:blank" ? "" : url;
    }
    if (destination?.type === "navigation") {
        const url = page.url();
        await page.goBack({ waitUntil: "domcontentloaded", timeout: 5000 }).catch(() => undefined);
        await page.waitForTimeout(500);
        return url;
    }
    return probedUrl;
}
async function restoreDoubaoWindowOpen(page) {
    return page.evaluate(() => {
        const probeKey = "__factCheckWindowOpenProbe";
        const probe = window[probeKey];
        if (!probe) {
            return "";
        }
        if (probe.originalOpen) {
            window.open = probe.originalOpen;
        }
        const url = String(probe.url || "");
        delete window[probeKey];
        return url;
    }).catch(() => "");
}
function shouldIgnoreDoubaoReference(rawUrl, text) {
    const lowerUrl = rawUrl.toLowerCase();
    return lowerUrl.includes("doubao.com/chat")
        || lowerUrl.includes("byteimg.com")
        || lowerUrl.includes("flow-web-cdn")
        || rawUrl.startsWith("#")
        || text === "用户协议"
        || text === "隐私政策";
}
async function extractDeepSeekReferences(page, baseUrl) {
    return page.locator(".ds-assistant-message-main-content a[href]").evaluateAll((nodes, base) => {
        const seen = new Set();
        const items = [];
        for (const node of nodes) {
            const anchor = node;
            const rawUrl = anchor.href || anchor.getAttribute("href") || "";
            if (!rawUrl) {
                continue;
            }
            const marker = anchor.querySelector(".ds-markdown-cite")?.textContent?.replace(/\D+/g, "") || undefined;
            const normalizedUrl = normalizeInBrowser(rawUrl, base);
            if (seen.has(normalizedUrl)) {
                continue;
            }
            seen.add(normalizedUrl);
            const title = anchor.getAttribute("title") || readableUrlTitle(normalizedUrl);
            const containerText = cleanDeepSeekCitationText(anchor.closest("p, li, div")?.textContent?.trim().replace(/\s+/g, " ") || "");
            items.push({
                title,
                url: normalizedUrl,
                normalizedUrl,
                marker,
                text: marker ? `[${marker}]` : undefined,
                answerContext: containerText ? containerText.slice(0, 500) : undefined
            });
        }
        return items;
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const url = new URL(rawUrl, baseUrl);
                url.hash = "";
                url.hostname = url.hostname.toLowerCase();
                if (url.pathname !== "/") {
                    url.pathname = url.pathname.replace(/\/+$/, "");
                }
                if (url.pathname === "/") {
                    url.pathname = "";
                }
                return url.toString().replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
        function cleanDeepSeekCitationText(text) {
            return text.replace(/-(\d{1,4})/g, "[$1]");
        }
        function readableUrlTitle(rawUrl) {
            try {
                const url = new URL(rawUrl);
                const path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
                return path ? `${url.hostname}/${path}` : url.hostname;
            }
            catch {
                return rawUrl;
            }
        }
    }, baseUrl);
}
async function extractYuanbaoReferences(page, baseUrl) {
    const sourceTool = page.locator("#search-guide-tool, [aria-label*='引用']").last();
    if (await sourceTool.isVisible().catch(() => false)) {
        await sourceTool.click().catch(() => undefined);
        await page.waitForTimeout(1000);
    }
    return page.locator(".hyc-common-markdown__ref_card").evaluateAll((nodes, base) => {
        const seen = new Set();
        const items = [];
        for (const node of nodes) {
            const element = node;
            const marker = element.getAttribute("data-idx") || undefined;
            const rawUrl = element.getAttribute("data-url") || `${base}#source-${marker || items.length + 1}`;
            const title = element.querySelector(".hyc-common-markdown__ref_card-title")?.textContent?.trim()
                || element.textContent?.trim()
                || rawUrl;
            const source = element.querySelector(".hyc-common-markdown__ref_card-foot__source_txt")?.textContent?.trim();
            const snippet = element.textContent?.trim().replace(/\s+/g, " ");
            const normalizedUrl = normalizeInBrowser(rawUrl, base);
            if (seen.has(normalizedUrl)) {
                continue;
            }
            seen.add(normalizedUrl);
            items.push({
                title,
                url: normalizedUrl,
                normalizedUrl,
                marker,
                text: source || title,
                snippet: snippet && snippet !== title ? snippet.slice(0, 500) : undefined,
                snippetProvenance: snippet && snippet !== title ? "source_surface" : undefined
            });
        }
        return items;
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const url = new URL(rawUrl, baseUrl);
                url.hash = "";
                url.hostname = url.hostname.toLowerCase();
                if (url.pathname !== "/") {
                    url.pathname = url.pathname.replace(/\/+$/, "");
                }
                if (url.pathname === "/") {
                    url.pathname = "";
                }
                return url.toString().replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
    }, baseUrl);
}
async function extractDknowcReferenceCards(locator, baseUrl, citationScope, sourceSection = "") {
    return locator.evaluateAll((nodes, options) => {
        const base = options.baseUrl;
        const ASSET_URL = /\.(?:png|jpe?g|gif|svg|webp|bmp|ico)(?:[?#]|$)/i;
        const FOOTNOTE_MARKER = /^\d{1,6}$/;
        const CARD_ITEM = ".jb-original-item[data-id], .chatsse-note-item";
        const seen = new Set();
        const items = [];
        for (const node of nodes) {
            const element = node;
            if (options.excludeModal && element.closest(".jb-modal")) {
                continue;
            }
            if (!element.hasAttribute("data-id") && element.querySelector(CARD_ITEM)) {
                continue;
            }
            const card = element.closest(".chat-jb, .chatsse-note-item, .jb-modal-note") || element;
            const marker = readMarker(element);
            const rawUrl = readUrl(element, card);
            if (!rawUrl) {
                continue;
            }
            const titleElement = card.querySelector(".chat-jb-title-text, .chat-jb-title-info, .czkjTitle");
            const citeElement = element.querySelector("cite") || card.querySelector("cite");
            const traceText = (element.getAttribute("data-text") || "").trim();
            const snippetElement = element.classList.contains("jb-original-item")
                ? element
                : card.querySelector(".scoresText, .jb-original-item");
            const snippet = (traceText || snippetElement?.textContent || "")
                .trim()
                .replace(/\s+/g, " ")
                .slice(0, 500);
            const normalizedUrl = rawUrl ? normalizeInBrowser(rawUrl, base) : "";
            const key = `${normalizedUrl}#${marker || ""}`;
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);
            const title = titleElement?.textContent?.trim()
                || citeElement?.textContent?.trim()
                || rawUrl
                || marker;
            items.push({
                title,
                url: rawUrl,
                normalizedUrl,
                marker,
                text: titleElement?.textContent?.trim() || undefined,
                snippet: snippet || undefined,
                snippetProvenance: snippet ? "source_surface" : undefined,
                citationScope: options.citationScope,
                sourceSection: options.sourceSection || citeElement?.textContent?.trim() || undefined,
                traceabilityText: traceText || undefined,
                linkStatus: "captured",
                platformTrustSource: "dknow_reference_capture"
            });
        }
        return items;
        function readMarker(element) {
            const own = (element.getAttribute("data-id") || "").trim();
            if (FOOTNOTE_MARKER.test(own)) {
                return own;
            }
            const scoreElement = element.querySelector(".chatsse-note-score-id, .hasColor, [data-id]");
            const scoped = (scoreElement?.getAttribute("data-id") || scoreElement?.textContent || "").trim();
            if (FOOTNOTE_MARKER.test(scoped)) {
                return scoped;
            }
            return own || scoped || undefined;
        }
        function readUrl(element, card) {
            const own = pickUrl([element]);
            if (own) {
                return own;
            }
            const inside = pickUrl(Array.from(element.querySelectorAll("[data-url2], [data-url]")));
            if (inside) {
                return inside;
            }
            const sameCard = Array.from(card.querySelectorAll("[data-url2], [data-url]"))
                .filter((candidate) => {
                    const owner = candidate.closest(".jb-original-item[data-id], .chatsse-note-item, .chat-jb");
                    return !owner || owner === card || owner === element;
                });
            return pickUrl(sameCard);
        }
        function pickUrl(elements) {
            const values = [];
            for (const element of elements) {
                const routed = element.getAttribute?.("data-url2");
                const plain = element.getAttribute?.("data-url");
                const href = element.getAttribute?.("href") || element.href;
                if (routed && routed.trim()) {
                    values.push(routed.trim());
                }
                if (plain && plain.trim()) {
                    values.push(plain.trim());
                }
                if (href && href.trim()) {
                    values.push(href.trim());
                }
            }
            return values.find((value) => !ASSET_URL.test(value)) || "";
        }
        function normalizeInBrowser(rawUrl, baseUrl) {
            try {
                const url = new URL(rawUrl, baseUrl);
                if (!url.hash.startsWith("#/")) {
                    url.hash = "";
                }
                url.hostname = url.hostname.toLowerCase();
                if (url.pathname !== "/") {
                    url.pathname = url.pathname.replace(/\/+$/, "");
                }
                if (url.pathname === "/") {
                    url.pathname = "";
                }
                const normalized = url.toString();
                return url.hash ? normalized : normalized.replace(/\/$/, "");
            }
            catch {
                return rawUrl.trim();
            }
        }
    }, { baseUrl, citationScope, sourceSection, excludeModal: citationScope === "inline" });
}

function mergeDknowcReference(references, incoming) {
    // 合并键必须同时包含来源 URL 与脚标：一份文件常常支撑多个脚标（如同一份办事指南
    // 被【103】【105】分别引用），只按 URL 合并会把后出现的脚标整条丢掉，导致这些脚标
    // 在回答里无法解析、相关主张被判证据不足。脚标缺失的条目（如无编号的知识专库来源）
    // 仍按 URL 与任意同源条目合并，保持“同一来源正文+知识专库只记一次”的原意。
    const urlOf = (reference) => String(reference.normalizedUrl || reference.url || "").trim();
    const markerOf = (reference) => String(reference.marker ?? "").trim();
    const titleOf = (reference) => String(reference.title || "").trim();
    const incomingUrl = urlOf(incoming);
    const incomingMarker = markerOf(incoming);
    const existing = references.find((reference) => {
        const sameUrl = urlOf(reference) === incomingUrl;
        const existingMarker = markerOf(reference);
        if (!incomingMarker || !existingMarker) {
            // 无脚标条目（如无编号的知识专库来源）仍按 URL 合并。
            return sameUrl;
        }
        if (existingMarker !== incomingMarker) {
            // 不同脚标即便指向同一份文件也必须各自保留，否则这些脚标无法解析。
            return false;
        }
        // 同一脚标同时给出原始政务网址与深知晓内部镜像时，是同一条引用的两个地址；
        // 镜像标题往往是原文标题去掉发布单位前缀后的子串，故按包含关系判定同源。
        if (sameUrl) {
            return true;
        }
        const existingTitle = titleOf(reference);
        const incomingTitle = titleOf(incoming);
        if (!existingTitle || !incomingTitle) {
            return false;
        }
        return existingTitle === incomingTitle
            || existingTitle.includes(incomingTitle)
            || incomingTitle.includes(existingTitle);
    });
    if (!existing) {
        references.push(incoming);
        return;
    }
    // 同一引用有多个地址时，保留可独立核验的外部来源，而不是平台内部镜像。
    const isPlatformMirror = (value) => /^https?:\/\/[^/]*dknowc\.cn\//i.test(String(value || ""));
    if (incoming.url && isPlatformMirror(existing.url) && !isPlatformMirror(incoming.url)) {
        existing.url = incoming.url;
        existing.normalizedUrl = incoming.normalizedUrl || incoming.url;
    }
    existing.title = longerText(existing.title, incoming.title);
    existing.text = longerText(existing.text, incoming.text);
    existing.snippet = longerText(existing.snippet, incoming.snippet);
    existing.content = longerText(existing.content, incoming.content);
    existing.traceabilityText = longerText(existing.traceabilityText, incoming.traceabilityText);
    existing.marker = existing.marker || incoming.marker;
    existing.sourceSection = existing.sourceSection || incoming.sourceSection;
    existing.linkStatus = existing.linkStatus || incoming.linkStatus;
    existing.platformTrustSource = existing.platformTrustSource || incoming.platformTrustSource;
    existing.citationScope = combineCitationScope(existing.citationScope, incoming.citationScope);
}

async function extractDknowcKnowledgeLibraryReferences(page, baseUrl) {
    const triggerCandidates = page.locator(".chatsse-data.chatSubBtn, .chatSubBtn");
    if (typeof triggerCandidates.filter !== "function") {
        return [];
    }
    const trigger = triggerCandidates
        .filter({ hasText: /\u77e5\u8bc6\u4e13\u5e93/ })
        .last();
    if (!(await trigger.isVisible().catch(() => false))) {
        return [];
    }
    if (!(await trigger.click({ timeout: 5000 }).then(() => true).catch(() => false))) {
        throw new Error("已发现深知晓知识专库入口，但自动点击失败，必须重新采集或由 Computer Use 接管。");
    }
    const modal = page.locator(".jb-modal").last();
    if (!(await modal.waitFor({ state: "visible", timeout: 5000 }).then(() => true).catch(() => false))) {
        throw new Error("已点击深知晓知识专库，但来源弹层未打开，必须重新采集或由 Computer Use 接管。");
    }
    const references = [];
    const collect = async () => {
        const cards = modal.locator(".jb-original-item[data-id], .chatsse-note-item, .chat-jb, .jb-modal-note");
        const current = await extractDknowcReferenceCards(cards, baseUrl, "global", "\u77e5\u8bc6\u4e13\u5e93").catch(() => []);
        for (const reference of current) {
            mergeDknowcReference(references, reference);
        }
    };
    try {
        await page.waitForTimeout(250);
        await collect();
        const options = modal.locator(
            ".jb-knowledge-data [data-id], .jb-knowledge-data li, .jb-knowledge-data button, .jb-knowledge-data [role='button']"
        );
        const count = Math.min(await options.count().catch(() => 0), 100);
        let visibleOptionCount = 0;
        for (let index = 0; index < count; index += 1) {
            const option = options.nth(index);
            if (!(await option.isVisible().catch(() => false))) {
                continue;
            }
            visibleOptionCount += 1;
            const clicked = await option.click({ timeout: 3000 })
                .then(() => true)
                .catch(() => false);
            if (!clicked) {
                throw new Error("深知晓知识专库中存在无法打开的来源项，必须重新采集或由 Computer Use 接管。");
            }
            await page.waitForTimeout(150);
            await collect();
        }
        if (visibleOptionCount > 0 && references.length === 0) {
            throw new Error("深知晓知识专库已展开，但未采集到任何可回溯来源，必须重新采集或由 Computer Use 接管。");
        }
    }
    finally {
        await modal.locator(".jb-modal-close").last().click({ timeout: 2000 }).catch(() => undefined);
    }
    return references;
}

export async function extractDknowcReferences(page, baseUrl, question = "") {
    const references = [];
    const inlineReferences = await extractDknowcReferenceCards(
        page.locator(".jb-original-item[data-id], .chatsse-note-item, .chat-jb"),
        baseUrl,
        "inline"
    );
    for (const reference of inlineReferences) {
        mergeDknowcReference(references, reference);
    }
    const globalReferences = await extractDknowcKnowledgeLibraryReferences(page, baseUrl);
    for (const reference of globalReferences) {
        mergeDknowcReference(references, reference);
    }
    await hydrateDknowcReferenceContent(references, question);
    return references;
}
function isDknowcPlatform(config) {
    return config.name === "dknowc-chat"
        || config.name === "dknowc-deep-research";
}
export async function activateDknowcDeepResearch(
    page,
    context,
    config,
    timeoutMs = 20000,
    ordinaryAnswer = ""
) {
    const selectors = config.selectors?.deepResearch || [
        "button:has-text('深度溯源')",
        "[role='button']:has-text('深度溯源')",
        "[class*='deep']:has-text('深度溯源')",
        "text=深度溯源",
        ".chatgpt-deepsearch.open",
        ".chatgpt-deepsearch[data-opens]",
        ".chatgpt-deepsearch.pointer",
        ".chatgpt-deepsearch"
    ];
    const action = await waitForFirstVisible(page, selectors, timeoutMs);
    if (!action) {
        throw new Error("普通回答已生成，但未找到可用的“深度溯源”入口。");
    }
    const pagesBefore = new Set(context.pages());
    const popupPromise = context.waitForEvent("page", { timeout: timeoutMs })
        .catch(() => undefined)
        .then((candidate) => candidate || new Promise(() => undefined));
    const clicked = await action.click({ timeout: timeoutMs })
        .then(() => true)
        .catch(() => false);
    if (!clicked) {
        throw new Error("已找到“深度溯源”入口，但自动点击失败，必须重新采集或由 Computer Use 接管。");
    }
    const inlinePromise = waitForDknowcDeepResearchSurface(
        page,
        config,
        ordinaryAnswer,
        timeoutMs
    ).then((found) => found ? page : undefined);
    let resultPage = await Promise.race([popupPromise, inlinePromise]);
    resultPage ||= context.pages().find((candidate) => !pagesBefore.has(candidate));
    if (!resultPage) {
        throw new Error("点击“深度溯源”后未出现结果，必须重新采集或由 Computer Use 接管。");
    }
    if (resultPage !== page && typeof resultPage.waitForURL === "function") {
        await resultPage.waitForURL(
            (url) => Boolean(url?.hostname && url.pathname !== "blank"),
            { timeout: timeoutMs }
        ).catch(() => undefined);
    }
    await resultPage.waitForLoadState("domcontentloaded", { timeout: timeoutMs })
        .catch(() => undefined);
    const resultUrl = resultPage.url();
    if (resultPage !== page && !isDknowcDeepResearchUrl(resultUrl, config.url)) {
        throw new Error(`“深度溯源”打开了非预期页面：${resultUrl}`);
    }
    return resultPage;
}
async function waitForDknowcDeepResearchSurface(page, config, ordinaryAnswer, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (page.isClosed?.()) {
            return false;
        }
        const answer = await extractAnswer(config, page).catch(() => "");
        if (answer && (!ordinaryAnswer || answer !== ordinaryAnswer)) {
            return true;
        }
        await page.waitForTimeout(400);
    }
    return false;
}
function isDknowcDeepResearchUrl(resultUrl, entryUrl) {
    try {
        const result = new URL(resultUrl);
        const entry = new URL(entryUrl);
        return result.hostname === entry.hostname
            && /\/wlcb\//i.test(result.pathname);
    }
    catch {
        return false;
    }
}
async function pageLooksLikeGate(page) {
    const title = (await page.title().catch(() => "")).toLowerCase();
    if (looksLikeLoginText(title)) {
        return true;
    }
    const bodyText = (await page.locator("body").innerText({ timeout: 2000 }).catch(() => "")).slice(0, 2000);
    return looksLikeLoginText(bodyText);
}
function looksLikeLoginText(value) {
    const text = value.toLowerCase();
    const markers = [
        "登录",
        "登陆",
        "手机号",
        "验证码",
        "滑动验证",
        "人机验证",
        "just a moment",
        "sign in",
        "log in",
        "verify you are human"
    ];
    return markers.some((marker) => text.includes(marker));
}
export function looksLikeNonAnswerPrompt(value) {
    const text = String(value || "").replace(/\s+/g, "");
    const markers = [
        "为您智能匹配到当前所在区域为",
        "如想咨询其他区域可点击修改",
        "请选择您想咨询的地区",
        "请选择咨询地区",
        "请先选择地区",
        "请选择所在地区",
        "请先完成登录",
        "登录后即可提问"
    ];
    const matchingMarkers = markers.filter((marker) => text.includes(marker.replace(/\s+/g, "")));
    if (matchingMarkers.length === 0) {
        return false;
    }
    const markerLength = matchingMarkers.reduce((total, marker) => total + marker.replace(/\s+/g, "").length, 0);
    return text.length <= Math.max(240, markerLength + 120);
}
export function looksLikeYuanbaoInterimAnswer(value) {
    const text = String(value || "").replace(/\s+/g, "");
    if (!text || text.length > 180) {
        return false;
    }
    return [
        "我来查一下",
        "我来搜索一下",
        "正在阅读资料",
        "正在为你查找",
        "正在检索"
    ].some((marker) => text.includes(marker));
}
export async function dismissYuanbaoGuides(page) {
    const selectors = [
        ".t-dialog__position .auto-search-guide-popup__button:has-text('我知道了')",
        ".yb-guide-popup__button:has-text('我知道了')",
        ".t-dialog__position button:has-text('我知道了')"
    ];
    for (const selector of selectors) {
        const candidates = page.locator(selector);
        const count = await candidates.count().catch(() => 0);
        for (let index = 0; index < count; index += 1) {
            const candidate = candidates.nth(index);
            if (await candidate.isVisible().catch(() => false)) {
                await candidate.click({ timeout: 2000 }).catch(() => undefined);
                await page.waitForTimeout(150);
            }
        }
    }
}
export function looksLikeLoginOnlyText(value) {
    const text = String(value || "").replace(/\s+/g, "");
    if (!text || text.length > 240) {
        return false;
    }
    const markers = [
        "请先登录",
        "登录后即可",
        "登录后继续",
        "会话过期，请重新登录",
        "账号登录",
        "扫码登录",
        "手机号登录",
        "滑动验证",
        "人机验证",
        "signin",
        "loginrequired",
        "verifyyouarehuman"
    ];
    const comparable = text.toLowerCase();
    return markers.some((marker) => comparable.includes(marker.replace(/\s+/g, "").toLowerCase()));
}
async function handleVerificationIfNeeded(config, page, options) {
    if (!(await pageLooksLikeVerification(page))) {
        return true;
    }
    if (!options.headed || !options.interactive) {
        return false;
    }
    console.log(`${config.label} 需要人工验证。请在当前 Playwright 页面完成后回复“验证已完成”；保持页面打开，采集器会自动复用原问题并继续，无需暂停、取消或回滚会话复制问题。`);
    return waitForVerificationClear(page, options.loginTimeoutMs || 300000);
}
async function pageLooksLikeVerification(page) {
    const bodyText = await page.locator("body").innerText({ timeout: 2000 }).catch(() => "");
    const markers = [
        "请选择所有符合",
        "拖拽到这里",
        "拖动下方滑块",
        "拖动滑块",
        "完成验证",
        "验证码",
        "滑动验证",
        "人机验证",
        "captcha",
        "verify"
    ];
    const comparable = bodyText.toLowerCase().replace(/\s+/g, "");
    return markers.some((marker) => comparable.includes(marker.toLowerCase().replace(/\s+/g, "")));
}
async function waitForVerificationClear(page, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline && await pageLooksLikeVerification(page)) {
        await page.waitForTimeout(1000);
    }
    return !(await pageLooksLikeVerification(page));
}
function captureStatusError(status, message, details = {}) {
    const error = new Error(message);
    error.captureStatus = status;
    Object.assign(error, details);
    return error;
}
function looksLikeLoadingText(value) {
    const markers = [
        "AI正在分析",
        "AI 正在分析",
        "AI 正翻阅",
        "AI正在溯源",
        "正在搜索网页",
        "再等一下",
        "请稍等",
        "loading"
    ];
    return markers.some((marker) => value.includes(marker));
}
async function captureDknowcDeepCompanion(
    ordinaryPage,
    context,
    config,
    options,
    ordinaryAnswer
) {
    const started = Date.now();
    const artifactDir = join(options.outDir, "artifacts", config.name);
    await ensureDir(artifactDir);
    let page = ordinaryPage;
    let answerMarkdown = "";
    let references = [];
    try {
        page = await activateDknowcDeepResearch(
            ordinaryPage,
            context,
            config,
            Math.min(options.timeoutMs, 20000),
            ordinaryAnswer
        );
        page.setDefaultTimeout(options.timeoutMs);
        page.setDefaultNavigationTimeout(options.timeoutMs);
        const deepResearchTimeoutMs = Number(config.deepResearchTimeoutMs)
            || options.timeoutMs;
        answerMarkdown = await waitForAnswer(
            config,
            page,
            deepResearchTimeoutMs,
            page === ordinaryPage ? ordinaryAnswer : "",
            options.question,
            {
                interactive: Boolean(options.headed && options.interactive),
                verificationTimeoutMs: options.loginTimeoutMs || 300000
            }
        );
        references = (await extractReferences(config, page, options.question)).references;
        const artifacts = await saveArtifacts(page, artifactDir, options.outDir);
        const validation = validateCapturedAnswer(config, answerMarkdown);
        if (validation) {
            return failure(config, validation.status, started, validation.error, artifacts, {
                answerMarkdown,
                references
            });
        }
        return success(config, started, answerMarkdown, references, [], artifacts);
    }
    catch (error) {
        const status = error && typeof error === "object" && "captureStatus" in error
            ? error.captureStatus
            : "failed";
        const artifacts = page
            ? await saveArtifacts(page, artifactDir, options.outDir).catch(() => undefined)
            : undefined;
        return failure(
            config,
            status,
            started,
            error instanceof Error ? error.message : String(error),
            artifacts,
            { answerMarkdown, references }
        );
    }
}
export function validateCapturedAnswer(config, answerMarkdown) {
    if (looksLikeLoginOnlyText(answerMarkdown)
        || looksLikeNonAnswerPrompt(answerMarkdown)
        || (config.name === "yuanbao" && looksLikeYuanbaoInterimAnswer(answerMarkdown))) {
        return {
            status: "login_required",
            error: "捕获内容仍是登录、地区选择或初始化提示，不是完整回答。"
        };
    }
    if (!answerMarkdown.trim()) {
        return {
            status: "failed",
            error: "No answer text detected."
        };
    }
    if (isDknowcPlatform(config)) {
        // 采集健全性门禁：占位符或过短文本不得以 success 落盘（深度溯源曾把“[检索完成]”记为成功）。
        const compact = answerMarkdown.replace(/\s+/g, "");
        // 仅剩固定提示语（正文尚未流出）同样不算回答。
        const withoutTips = compact.replace(/^AI综合所有相关权威材料后[，,]?参考性解读如下[，,]?(?:建议点击角标查看所依据的材料原文)?[。.]?/, "");
        if (looksLikeDknowcDeepResearchProgress(compact) || compact.length < 30 || !withoutTips) {
            return {
                status: "failed",
                error: `深知晓回答仅为进度占位或过短文本（${compact.slice(0, 30)}），判定采集未完成，不记为成功；请重新采集。`
            };
        }
    }
    return undefined;
}
function success(config, started, answerMarkdown, references, sourceMentions, artifacts, sourceCountAudit) {
    const result = {
        platform: config.name,
        label: config.label,
        url: config.url,
        status: "success",
        answerMarkdown,
        references,
        sourceMentions,
        artifacts,
        durationMs: Date.now() - started
    };
    if (sourceCountAudit) {
        result.sourceCountAudit = sourceCountAudit;
    }
    return result;
}
function failure(config, status, started, error, artifacts, partial = {}) {
    return {
        platform: config.name,
        label: config.label,
        url: config.url,
        status,
        answerMarkdown: partial.answerMarkdown || "",
        references: Array.isArray(partial.references) ? partial.references : [],
        sourceMentions: Array.isArray(partial.sourceMentions) ? partial.sourceMentions : [],
        ...(partial.sourceCountAudit ? { sourceCountAudit: partial.sourceCountAudit } : {}),
        artifacts,
        durationMs: Date.now() - started,
        error
    };
}
async function saveArtifacts(page, artifactDir, outDir) {
    const screenshotPath = join(artifactDir, "screenshot.png");
    const htmlPath = join(artifactDir, "page.html");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    await writeTextFile(htmlPath, await page.content());
    return {
        screenshot: relativeArtifact(screenshotPath, outDir),
        html: relativeArtifact(htmlPath, outDir)
    };
}
export function relativeArtifact(path, outDir) {
    const artifactPath = String(path);
    const outputRoot = String(outDir);
    const windowsPath = win32.isAbsolute(artifactPath) || win32.isAbsolute(outputRoot);
    const relativePath = windowsPath
        ? win32.relative(outputRoot, artifactPath)
        : relative(outputRoot, artifactPath);
    const normalized = relativePath.replace(/\\/g, "/");
    if (
        !normalized.startsWith("artifacts/")
        || normalized.split("/").includes("..")
        || normalized.startsWith("/")
        || win32.isAbsolute(normalized)
    ) {
        throw new Error(`采集存证路径越出输出目录：${artifactPath}`);
    }
    return normalized;
}
