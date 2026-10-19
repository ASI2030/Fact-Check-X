#!/usr/bin/env node
import { Command } from "commander";
import { existsSync } from "node:fs";
import { copyFile, mkdir, readdir, readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";
import { join, dirname, resolve, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { authenticationRequired, waitForAuthentication } from "./capture/auth-state.js";
import { openBrowserSession } from "./capture/browser-session.js";
import { captureGenericChat, hydrateDirectSourceReferences } from "./capture/generic-chat.js";
import { resumeExistingCapture } from "./capture/resume.js";
import { buildLoginRecovery } from "./capture/login-recovery.js";
import { captureDknowcChat } from "./capture/providers/dknowc-chat.js";
import { listPlatformConfigs, resolvePlatformTarget } from "./capture/platform-registry.js";
import { renderHtmlReport } from "./report/html-report.js";
import { renderMarkdownReport } from "./report/markdown-report.js";
import { parseRunResult } from "./schema/result.js";
import { ensureDir, readJsonFile, writeJsonFile, writeTextFile } from "./utils/filesystem.js";
import { profileDirectory } from "./utils/profile.js";
import { normalizeUrl } from "./utils/urls.js";
export async function generateReportFiles(run, outDir) {
    await ensureDir(outDir);
    await writeJsonFile(join(outDir, "results.json"), run);
    await writeTextFile(join(outDir, "report.html"), renderHtmlReport(run));
    await writeTextFile(join(outDir, "report.md"), renderMarkdownReport(run));
}
async function reportCommand(input, outDir) {
    const run = parseRunResult(await readJsonFile(input));
    await generateReportFiles(run, outDir);
    console.log(`报告已写入 ${outDir}`);
}
async function loginCommand(platformTarget, timeoutMs, outDir, question) {
    const config = resolvePlatformTarget(platformTarget);
    const profileDir = profileDirectory(config.profile);
    let session;
    let saved = false;
    try {
        session = await openBrowserSession(profileDir, config.url, {
            headed: true,
            interactive: true
        });
        const page = session.page;
        await page.goto(config.url, { waitUntil: "domcontentloaded" });
        if (config.requiresLogin) {
            await page.waitForTimeout(1500);
        }
        console.log(`已打开 ${config.label} 浏览器。首次使用请完成登录；检测到可提问界面后将自动保存会话。`);
        let loginRequired = await authenticationRequired(page, config);
        if (!loginRequired) {
            loginRequired = !(await waitForAuthentication(page, config, Math.min(timeoutMs, 5000)));
        }
        if (loginRequired) {
            console.log(`${config.label} 当前未登录。请在浏览器完成登录；登录入口消失前不会保存会话。`);
            const authenticated = await waitForAuthentication(page, config, timeoutMs);
            if (!authenticated) {
                throw new Error(`${config.label} 在 ${Math.round(timeoutMs / 1000)} 秒内未完成登录，请重新运行登录准备。`);
            }
        }
        const ready = await waitForChatReady(page, config, timeoutMs);
        if (!ready) {
            throw new Error(`${config.label} 在 ${Math.round(timeoutMs / 1000)} 秒内未检测到已登录的可提问界面，请重新运行登录准备。`);
        }
        saved = true;
    }
    catch (error) {
        const recovery = buildLoginRecovery(config, question, error);
        if (outDir) {
            await ensureDir(outDir);
            await writeJsonFile(join(outDir, "capture-recovery.json"), recovery);
            console.error(`登录准备未完成，已写出 ${join(outDir, "capture-recovery.json")}。`);
        }
        console.error("当前载体具备 Computer Use 时必须用它恢复同一平台；否则停止在原始答案采集阶段。禁止改用 headless、另一套浏览器或命令行诊断。");
        throw new Error(`登录准备未完成，已停止在原始答案采集阶段并请求 Computer Use 恢复：${recovery.failedPlatforms[0].error}`);
    }
    finally {
        if (session) {
            await session.release();
        }
    }
    if (saved) {
        console.log(`已保存 ${config.label} 登录状态：${profileDir}`);
    }
}

async function waitForChatReady(page, config, timeoutMs) {
    const selectors = (config.selectors?.input || ["textarea", "[contenteditable='true']", "div[role='textbox']"])
        .filter((selector) => !selector.includes("input[type='text']"));
    const deadline = Date.now() + timeoutMs;
    let consecutiveReadyChecks = 0;
    while (Date.now() < deadline) {
        if (await authenticationRequired(page, config)) {
            consecutiveReadyChecks = 0;
            await page.waitForTimeout(1000);
            continue;
        }
        let inputVisible = false;
        for (const selector of selectors) {
            const locator = page.locator(selector);
            const count = await locator.count().catch(() => 0);
            for (let index = count - 1; index >= 0; index -= 1) {
                if (await locator.nth(index).isVisible().catch(() => false)) {
                    inputVisible = true;
                    break;
                }
            }
            if (inputVisible) {
                break;
            }
        }
        if (inputVisible) {
            consecutiveReadyChecks += 1;
            if (consecutiveReadyChecks >= 3) {
                return true;
            }
        }
        else {
            consecutiveReadyChecks = 0;
        }
        await page.waitForTimeout(700);
    }
    return false;
}
async function runCommand(options) {
    await ensureFreshCaptureOutput(options.out, options.question);
    const timeoutMs = positiveNumber(options.timeout, 180000);
    const retryCount = nonnegativeInteger(options.retries, 2);
    const retryDelayMs = nonnegativeInteger(options.retryDelay, 3000);
    const configs = options.platform.map(resolvePlatformTarget);
    const resultsByPlatform = new Map();
    for (const { config, deepCompanionConfig } of buildCapturePlan(configs)) {
        const loginTimeoutMs = positiveNumber(options.loginTimeout, 300000);
        const result = await captureWithRetries(config, {
            question: options.question,
            outDir: options.out,
            headed: Boolean(options.headed || options.interactive),
            interactive: Boolean(options.interactive),
            timeoutMs,
            loginTimeoutMs,
            retryCount,
            retryDelayMs,
            deepCompanionConfig
        });
        const { companionResult, ...primaryResult } = result;
        resultsByPlatform.set(config.name, primaryResult);
        if (companionResult) {
            resultsByPlatform.set(companionResult.platform, companionResult);
        }
        else if (deepCompanionConfig) {
            resultsByPlatform.set(deepCompanionConfig.name, dependentCaptureFailure(
                deepCompanionConfig,
                result
            ));
        }
    }
    const platforms = configs.map((config) => resultsByPlatform.get(config.name));
    const run = parseRunResult({
        schemaVersion: "1",
        question: options.question,
        createdAt: new Date().toISOString(),
        platforms
    });
    await generateReportFiles(run, options.out);
    console.log(`报告已写入 ${options.out}`);
    const incomplete = platforms.filter((platform) => platform.status !== "success");
    if (incomplete.length > 0) {
        await writeJsonFile(join(options.out, "capture-recovery.json"), {
            schemaVersion: "fact-check-x/capture-recovery@2",
            status: "required",
            action: "computer_use",
            agentMustAct: true,
            preferredTools: ["host_browser", "computer_use"],
            createdAt: new Date().toISOString(),
            question: options.question,
            failedPlatforms: incomplete.map((platform) => ({
                platform: platform.platform,
                label: platform.label,
                url: normalizeUrl(platform.url),
                loginUrl: normalizeUrl(platform.url),
                status: platform.status,
                error: platform.error,
                captureLifecycle: platform.captureLifecycle,
                selectorRecovery: platform.selectorRecovery
                ,resumeUrl: platform.captureLifecycle?.conversationUrl || normalizeUrl(platform.url)
            })),
            instructions: [
                "Agent必须检查当前可用工具并实际调用浏览器工具；需要页面视觉交互时使用Computer Use。不能只把恢复步骤转交用户。",
                "使用resumeUrl定位原会话并核对原问题；已提交的任务只等待、展开来源或重新提取，不重复发问。",
                "浏览器工具恢复页面后调用resume；程序选择器仍不兼容时，可由浏览器工具直接读取原答、引用和正文并保存现场，再按相同数据契约验收。不得以模型补写或普通搜索替代原答。",
                "优先使用当前运行载体自带的浏览器或 Computer Use 诊断同一页面；agent-browser 仅是可选诊断工具，不是运行依赖。",
                "需要用户本人处理账号、密码、验证码或人机验证。",
                "仅使用 failedPlatforms[].loginUrl 打开平台；该字段是已清洗的纯 URL，不得拼接说明文字或展示层追踪参数。",
                "captureLifecycle.submissionAttempted 或 answerObserved 为 true 时，禁止重新提交、重输或重问；只能恢复原会话并继续等待、定位或提取现有回答。",
                "仅在 captureLifecycle.resubmissionAllowed 为 true 时允许提交一次原始问题；提交后立即转为只读恢复。",
                "selectorRecovery 中的候选只是建议，必须通过程序的唯一、可见、可编辑或非进度文本门禁后才能采用；模型或浏览器工具不得直接改写适配器。",
                "接管后保持当前会话；等待人工验证时不得关闭、重复打开浏览器或机械重采。",
                "全部平台成功前禁止进入知识点对比。",
                "failedPlatforms[].status 为 input_not_found 时不是登录问题：应先由恢复层寻找并验证新输入框；验证失败后再更新适配器。"
            ]
        });
        const details = incomplete
            .map((platform) => `${platform.label}: ${platform.status} (${platform.error || "未知原因"})`)
            .join("；");
        throw new Error(`采集未完成，已停止流水线并请求 Computer Use 恢复，禁止进入知识点对比：${details}`);
    }
    await writeJsonFile(join(options.out, "capture-recovery.json"), {
        schemaVersion: "fact-check-x/capture-recovery@2",
        status: "not_required",
        action: "none",
        createdAt: new Date().toISOString(),
        question: options.question,
        failedPlatforms: []
    });
}
export async function ensureFreshCaptureOutput(outDir, question) {
    const resultsPath = join(outDir, "results.json");
    if (!existsSync(resultsPath)) {
        return;
    }
    let existing;
    try {
        existing = await readJsonFile(resultsPath);
    }
    catch (error) {
        throw new Error(`运行目录已有无法解析的 results.json，已拒绝重新提交问题以避免覆盖现场：${resultsPath}。请保留该目录诊断；确需开始新测试时使用新的运行目录。`);
    }
    const existingPlatforms = Array.isArray(existing?.platforms)
        ? existing.platforms.map((platform) => platform?.label || platform?.platform).filter(Boolean)
        : [];
    const sameQuestion = String(existing?.question || "").trim() === String(question || "").trim();
    const detail = existingPlatforms.length > 0
        ? `已有平台：${existingPlatforms.join("、")}`
        : "已有采集结果";
    throw new Error(`运行目录已有 results.json（${detail}${sameQuestion ? "；问题相同" : "；问题不同"}），run 已拒绝重新提交或覆盖。恢复当前任务时只能在原会话继续等待、定位和提取；开始新的采集必须使用新的运行目录。`);
}
export async function resumeCommand(options, capture = resumeExistingCapture) {
    const input = resolve(options.input);
    const sourceDir = dirname(input);
    const out = resolve(options.out);
    if (out === sourceDir || (existsSync(out) && (await readdir(out)).length)) {
        throw new Error("续采必须使用新的空目录，禁止覆盖原始现场。");
    }
    const inputBytes = await readFile(input);
    const run = parseRunResult(JSON.parse(inputBytes));
    const prior = run.platforms.find(item => item.platform === options.platform);
    if (!prior || prior.status === "success") throw new Error("只能恢复原任务中的失败平台，不能删减或重新采集已成功平台。");
    const config = resolvePlatformTarget(options.platform);
    const conversationUrl = options.conversationUrl || prior.captureLifecycle?.conversationUrl;
    if (!conversationUrl) throw new Error("旧结果未保存原会话URL；请提供已核实的 --conversation-url，不得改用平台首页重问。");
    const resumed = await capture(config, prior, {
        question: run.question, conversationUrl, outDir: out,
        timeoutMs: positiveNumber(options.timeout, 600000),
        loginTimeoutMs: positiveNumber(options.loginTimeout, 300000),
        headed: true, interactive: Boolean(options.interactive)
    });
    if (resumed.platform !== prior.platform || resumed.captureLifecycle?.recoverySubmissionCount !== 0) {
        throw new Error("续采平台或零提交约束不一致。");
    }
    await ensureDir(out);
    for (const platform of run.platforms) {
        if (platform.platform === prior.platform) continue;
        for (const artifact of Object.values(platform.artifacts || {})) {
            const source = resolve(sourceDir, artifact);
            const rel = relative(sourceDir, source);
            if (isAbsolute(rel) || rel.startsWith("..")) throw new Error("存证路径超出原采集目录。");
            const destination = join(out, rel);
            await mkdir(dirname(destination), { recursive: true });
            await copyFile(source, destination);
        }
    }
    const recovered = parseRunResult({ ...run, createdAt: new Date().toISOString(),
        platforms: run.platforms.map(item => item.platform === prior.platform ? resumed : item),
        recovery: { sourceResultsSha256: createHash("sha256").update(inputBytes).digest("hex"),
            platform: prior.platform, conversationUrl, submissionCount: 0 } });
    await generateReportFiles(recovered, out);
    const failed = recovered.platforms.filter(item => item.status !== "success");
    await writeJsonFile(join(out, "capture-recovery.json"), {
        schemaVersion: "fact-check-x/capture-recovery@2", status: failed.length ? "required" : "not_required",
        question: run.question, failedPlatforms: failed, action: failed.length ? "computer_use" : "none",
        agentMustAct: failed.length > 0, preferredTools: ["host_browser", "computer_use"],
        instructions: failed.length ? ["Agent主动调用浏览器工具或Computer Use恢复原会话，保持原问题和平台集合；只在扫码、验证码等本人步骤请用户介入。"] : []
    });
    if (failed.length) throw new Error("原会话续采尚未完成；失败平台仍保留，禁止进入知识点对比。");
    console.log(`原会话续采完成（新增提交0次）：${out}`);
    return recovered;
}
export function buildCapturePlan(configs) {
    const dknowcChat = configs.find((config) => config.name === "dknowc-chat");
    const dknowcDeepResearch = configs.find((config) => config.name === "dknowc-deep-research");
    return configs
        .filter((config) => !(config.name === "dknowc-deep-research" && dknowcChat))
        .map((config) => ({
            config,
            deepCompanionConfig: config.name === "dknowc-chat"
                ? dknowcDeepResearch
                : undefined
        }));
}
export async function hydrateSourcesCommand(options) {
    const input = resolve(options.input);
    const sourceDir = resolve(options.artifactRoot || dirname(input));
    const out = resolve(options.out);
    if (out === sourceDir || (existsSync(out) && (await readdir(out)).length)) {
        throw new Error("正文补全必须使用新的空目录，保留原始采集结果。");
    }
    const original = await readFile(input);
    const run = parseRunResult(JSON.parse(original));
    const platform = run.platforms.find(item => item.platform === options.platform);
    const indexes = [...new Set(options.reference.map(Number))];
    if (!platform || indexes.some(index => !Number.isInteger(index) || index < 1 || index > platform.references.length)) {
        throw new Error("平台或来源索引不属于原采集结果。");
    }
    const selected = indexes.map(index => platform.references[index - 1]);
    for (const reference of selected) {
        if (reference.sourceContentScope === "platform_excerpt") reference.platformExcerpt = reference.content;
    }
    const profile = await mkdtemp(join(tmpdir(), "fact-check-x-source-"));
    let session;
    try {
        session = await openBrowserSession(profile, "about:blank", { headed: true });
        await hydrateDirectSourceReferences(session.page, selected, platform.url);
    } finally {
        await session?.release();
        await rm(profile, { recursive: true, force: true });
    }
    await ensureDir(out);
    for (const item of run.platforms) {
        for (const artifact of Object.values(item.artifacts || {})) {
            const source = resolve(sourceDir, artifact);
            const rel = relative(sourceDir, source);
            if (isAbsolute(rel) || rel.startsWith("..")) throw new Error("存证路径超出原采集目录。");
            await mkdir(dirname(join(out, rel)), { recursive: true });
            await copyFile(source, join(out, rel));
        }
    }
    const failed = [];
    for (let position = 0; position < selected.length; position++) {
        const reference = selected[position];
        if (reference.sourceAcquisitionStatus === "captured") reference.sourceContentScope = "document_extraction";
        else failed.push(indexes[position]);
    }
    run.recovery = { mode: "source_hydration", platform: platform.platform, referenceIndexes: indexes,
        sourceResultsSha256: createHash("sha256").update(original).digest("hex"), submissionCount: 0, failedReferenceIndexes: failed };
    run.createdAt = new Date().toISOString();
    await generateReportFiles(run, out);
    if (failed.length) throw new Error(`来源正文仍未取得：${failed.join(",")}；错误状态和原片段已保留。`);
    console.log(`原来源正文补全完成（新增提问0次）：${out}`);
    return run;
}
export async function captureWithRetries(config, options, capture = capturePlatform, wait = sleep) {
    const maxAttempts = options.retryCount + 1;
    let result;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        console.log(`正在采集 ${config.label}（第 ${attempt}/${maxAttempts} 次，最长等待 ${Math.round(options.timeoutMs / 1000)} 秒）`);
        result = await capture(config, options);
        const companionSucceeded = !result.companionResult
            || result.companionResult.status === "success";
        if (result.status === "success" && companionSucceeded) {
            console.log(`${config.label} 已确认采集完成。`);
            break;
        }
        const failedResult = result.status === "success"
            ? result.companionResult
            : result;
        console.log(`${failedResult.label || config.label} 本次采集未完成：${failedResult.status}；${failedResult.error || "未知原因"}`);
        if (failedResult.status === "input_not_found") {
            console.log(`${failedResult.label || config.label} 未找到提问输入框，这不是登录问题：平台界面可能已变化，需要更新适配器选择器。已停止机械重采并保留原始问题。`);
            break;
        }
        if (["login_required", "verification_required"].includes(failedResult.status)) {
            console.log(`${failedResult.label || config.label} 需要人工接管。已停止机械重采并保留原始问题；请根据 capture-recovery.json 继续。`);
            break;
        }
        const lifecycle = failedResult.captureLifecycle || result.captureLifecycle || {};
        if (lifecycle.submissionAttempted || lifecycle.submissionConfirmed || lifecycle.answerObserved) {
            console.log(`${failedResult.label || config.label} 已尝试提交或已出现回答；为防止重复提问，停止整轮重采，只允许在原会话恢复选择器、等待或提取。`);
            break;
        }
        if (attempt < maxAttempts) {
            console.log(`${Math.round(options.retryDelayMs / 1000)} 秒后重新采集 ${config.label}。`);
            await wait(options.retryDelayMs);
        }
    }
    return result;
}
function dependentCaptureFailure(config, primaryResult) {
    return {
        platform: config.name,
        label: config.label,
        url: config.url,
        status: primaryResult.status === "success" ? "failed" : primaryResult.status,
        answerMarkdown: "",
        references: [],
        sourceMentions: [],
        captureLifecycle: primaryResult.captureLifecycle,
        selectorRecovery: primaryResult.selectorRecovery,
        durationMs: primaryResult.durationMs,
        error: `普通深知晓未成功，未启动深度溯源：${primaryResult.error || "采集未完成"}`
    };
}
async function capturePlatform(config, options) {
    if (config.adapter === "dknowc-chat") {
        return captureDknowcChat(config, options);
    }
    return captureGenericChat(config, options);
}
function sleep(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
function positiveNumber(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}
function nonnegativeInteger(value, fallback) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback;
}
export function createProgram() {
    const program = new Command();
    program
        .name("llm-compare")
        .description("无损采集并对比多个 AI 网页端的原始回答与引用。")
        .version("0.1.0");
    program
        .command("report")
        .description("从已有 results.json 生成各方答案汇总。")
        .requiredOption("--input <path>", "results.json 路径")
        .requiredOption("--out <dir>", "输出目录")
        .action(async (options) => {
        await reportCommand(options.input, options.out);
    });
    program
        .command("run")
        .description("从平台网页采集原始回答与引用并生成报告。")
        .requiredOption("--question <question>", "提交给各平台的用户问题")
        .requiredOption("--platform <target>", "平台名或 name=url 自定义目标", collect, [])
        .requiredOption("--out <dir>", "输出目录")
        .option("--headed", "显示浏览器窗口")
        .option("--interactive", "允许手工处理登录、二维码、验证码或其他验证")
        .option("--timeout <ms>", "回答生成完成等待毫秒数", "180000")
        .option("--login-timeout <ms>", "首次登录等待毫秒数", "300000")
        .option("--retries <count>", "每个平台失败后的自动重采次数", "2")
        .option("--retry-delay <ms>", "重采间隔毫秒数", "3000")
        .action(async (options) => {
        await runCommand(options);
    });
    program
        .command("hydrate-sources")
        .description("按模型选定的原来源补全正文，不重新提问或用其他材料替换。")
        .requiredOption("--input <path>", "原采集 results.json")
        .requiredOption("--platform <name>", "原平台")
        .requiredOption("--reference <index>", "需要全文的原来源序号，可重复", collect, [])
        .requiredOption("--out <dir>", "新的空目录")
        .option("--artifact-root <dir>", "报告包中原截图与页面的相对路径根目录")
        .action(options => hydrateSourcesCommand(options));
    program
        .command("resume")
        .description("在原会话延长等待或重提取，不发送问题；保留原平台集合和旧产物。")
        .requiredOption("--input <path>", "原采集 results.json")
        .requiredOption("--platform <name>", "原任务中失败的平台")
        .requiredOption("--out <dir>", "新的空输出目录")
        .option("--conversation-url <url>", "旧版未记录时提供已核实的原会话URL")
        .option("--interactive", "允许本人在原会话处理登录或验证")
        .option("--timeout <ms>", "延长等待毫秒数", "600000")
        .option("--login-timeout <ms>", "等待登录毫秒数", "300000")
        .action(options => resumeCommand(options));
    program
        .command("login")
        .description("首次使用时打开可见浏览器，提示登录并在检测到聊天界面后保存会话。")
        .requiredOption("--platform <target>", "平台名或 name=url 自定义目标")
        .option("--question <question>", "需要在 Computer Use 恢复时复用的原始问题", "")
        .option("--out <dir>", "登录失败时写入 capture-recovery.json 的采集目录")
        .option("--timeout <ms>", "等待登录完成的毫秒数", "300000")
        .action(async (options) => {
        await loginCommand(options.platform, Number(options.timeout || 300000), options.out, options.question);
    });
    program
        .command("platforms")
        .description("列出内置平台。")
        .action(() => {
        for (const platform of listPlatformConfigs()) {
            console.log(`${platform.name}\t${platform.label}\t${platform.url || "(custom URL)"}`);
        }
    });
    return program;
}
function collect(value, previous) {
    previous.push(value);
    return previous;
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
    createProgram().parseAsync(process.argv).catch((error) => {
        console.error(error instanceof Error ? error.message : String(error));
        process.exitCode = 1;
    });
}
