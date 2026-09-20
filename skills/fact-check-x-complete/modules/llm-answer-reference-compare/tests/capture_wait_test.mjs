import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
    authenticationRequired,
    waitForAuthentication
} from "../assets/tool/dist/capture/auth-state.js";
import {
    activateDknowcDeepResearch,
    confirmPromptSubmission,
    createDoubaoStreamCompletionMonitor,
    extractDknowcAnswer,
    extractDoubaoSourceMentions,
    isPdfReference,
    looksLikeDoubaoInterimAnswer,
    looksLikeLoginOnlyText,
    looksLikeNonAnswerPrompt,
    looksLikeYuanbaoInterimAnswer,
    submitPromptAndConfirm,
    validateCapturedAnswer,
    waitForAnswer
} from "../assets/tool/dist/capture/generic-chat.js";
import { builtInPlatforms } from "../assets/tool/dist/capture/platform-registry.js";
import { buildCapturePlan, captureWithRetries, ensureFreshCaptureOutput } from "../assets/tool/dist/cli.js";
import { normalizeUrl } from "../assets/tool/dist/utils/urls.js";

const started = Date.now();
const deepResearchConfig = builtInPlatforms.find(
    (platform) => platform.name === "dknowc-deep-research"
);
assert.equal(deepResearchConfig.label, "深知晓（深度溯源）");
assert.equal(
    deepResearchConfig.url,
    "https://yun.dknowc.cn/wlcb/szx/#/"
);
assert.equal(deepResearchConfig.profile, "dknowc-chat");
assert.equal(deepResearchConfig.selectors.send[0], ".czkj-enter-btn.actived");
const pairedPlan = buildCapturePlan([
    builtInPlatforms.find((platform) => platform.name === "dknowc-chat"),
    deepResearchConfig,
]);
assert.equal(pairedPlan.length, 1);
assert.equal(pairedPlan[0].config.name, "dknowc-chat");
assert.equal(pairedPlan[0].deepCompanionConfig.name, "dknowc-deep-research");

const dknowHomepagePage = {
    locator(selector) {
        return {
            last() { return this; },
            async isVisible() { return false; },
            async evaluateAll(callback) {
                assert.equal(selector, ".czkj-robot:not(.chat-load-text) .czkj-msg");
                return callback([{
                    innerText: "首页推荐问题，不是回答",
                    closest() { return {}; }
                }]);
            }
        };
    }
};
assert.equal(await extractDknowcAnswer(dknowHomepagePage), "");

const deepResultPage = {
    async waitForLoadState() {},
    url() {
        return "https://yun.dknowc.cn/wlcb/SDSYbaogao/?uid=test";
    }
};
let deepResearchClicked = 0;
const deepResearchPage = {
    locator(selector) {
        assert.equal(selector, "button:has-text('深度溯源')");
        return {
            last() {
                return this;
            },
            nth() {
                return this;
            },
            async count() {
                return 1;
            },
            async isVisible() {
                return true;
            },
            async click() {
                deepResearchClicked += 1;
            }
        };
    },
    async waitForTimeout() {}
};
const deepResearchContext = {
    pages() {
        return [deepResearchPage];
    },
    async waitForEvent(event) {
        assert.equal(event, "page");
        return deepResultPage;
    }
};
assert.equal(
    await activateDknowcDeepResearch(
        deepResearchPage,
        deepResearchContext,
        deepResearchConfig,
        1000
    ),
    deepResultPage
);
assert.equal(deepResearchClicked, 1);

const page = {
    locator(selector) {
        return {
            last() {
                return this;
            },
            async count() {
                return selector === ".answer" || selector.includes("停止") ? 1 : 0;
            },
            async isVisible() {
                return selector.includes("停止") && Date.now() - started < 320;
            },
            async innerText() {
                const elapsed = Date.now() - started;
                if (elapsed < 100) {
                    return "为您智能匹配到当前所在区域为“北京市”，如想咨询其他区域可点击修改";
                }
                if (elapsed < 220) {
                    return "每人每月最高提取";
                }
                if (elapsed < 320) {
                    return "每人每月最高提取 1400";
                }
                return "每人每月最高提取 1400 元。";
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 50)));
    }
};

assert.equal(
    looksLikeNonAnswerPrompt("为您智能匹配到当前所在区域为“北京市”，如想咨询其他区域可点击修改"),
    true
);
assert.equal(
    looksLikeNonAnswerPrompt("北京高考报名分为网上申请、填报缴费和现场确认三个阶段。".repeat(8) + " 页面底部：为您智能匹配到当前所在区域为“北京市”，如想咨询其他区域可点击修改"),
    false
);
assert.equal(looksLikeYuanbaoInterimAnswer("我来查一下深圳夫妻投靠入户的最新政策要求。"), true);
assert.equal(looksLikeYuanbaoInterimAnswer("我来查一下。经核验，以下是完整政策条件。".repeat(12)), false);
assert.equal(looksLikeDoubaoInterimAnswer("正在查证"), true);
assert.equal(validateCapturedAnswer({ name: "doubao" }, "正在查证")?.status, "failed");
const realDoubaoInterim = "官方来源已确认现行门槛仍是国科发火〔2016〕32 号规定的标准。我再核对一下科技部官网原文，确保引用的条文准确。";
assert.equal(looksLikeDoubaoInterimAnswer(realDoubaoInterim), true);
assert.equal(validateCapturedAnswer({ name: "doubao" }, realDoubaoInterim)?.status, "failed");
const observedDoubaoInterim = "搜索结果已经指向官方文件。我再精读科技部官网原文，确认条款全文和现行有效性。";
assert.equal(looksLikeDoubaoInterimAnswer(observedDoubaoInterim), true);
assert.equal(validateCapturedAnswer({ name: "doubao" }, observedDoubaoInterim)?.status, "failed");
const reproducedDoubaoInterim = "我来查一下深圳市小微企业招用离校 2 年内未就业高校毕业生社保补贴的现行政策依据。";
assert.equal(looksLikeDoubaoInterimAnswer(reproducedDoubaoInterim), true);
assert.equal(validateCapturedAnswer({ name: "doubao" }, reproducedDoubaoInterim)?.status, "failed");
let doubaoResponseListener;
const streamMonitorContext = {
    on(event, listener) {
        assert.equal(event, "response");
        doubaoResponseListener = listener;
    },
    off(event, listener) {
        assert.equal(event, "response");
        assert.equal(listener, doubaoResponseListener);
    }
};
const streamMonitorPage = {
    context() {
        return streamMonitorContext;
    }
};
const completedStreamMonitor = createDoubaoStreamCompletionMonitor(streamMonitorPage);
completedStreamMonitor.markSubmitted();
doubaoResponseListener({
    url() {
        return "https://www.doubao.com/alice/message/stream_reply";
    },
    async finished() {
        return null;
    },
    async text() {
        return "event: pb\ndata: payload\n\nevent: done\ndata:\n\n";
    }
});
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(completedStreamMonitor.snapshot().started, true);
assert.equal(completedStreamMonitor.snapshot().completed, true);
assert.equal(completedStreamMonitor.snapshot().failed, false);
assert.equal(completedStreamMonitor.snapshot().endpoint, "/alice/message/stream_reply");
completedStreamMonitor.markSubmitted();
doubaoResponseListener({
    url() {
        return "https://www.doubao.com/chat/completion";
    },
    status() {
        return 200;
    },
    async finished() {
        return null;
    },
    async text() {
        throw new Error("The current stream must not require buffering its body.");
    }
});
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(completedStreamMonitor.snapshot().started, true);
assert.equal(completedStreamMonitor.snapshot().completed, true);
assert.equal(completedStreamMonitor.snapshot().failed, false);
assert.equal(completedStreamMonitor.snapshot().endpoint, "/chat/completion");
completedStreamMonitor.dispose();
assert.equal(looksLikeDoubaoInterimAnswer("已定位当前政策，接下来我复核官方原文。"), true);
assert.equal(
    looksLikeDoubaoInterimAnswer("经核对，研发费用占比按收入分档为 5%、4%、3%；高新技术产品收入占比不低于 60%。"),
    false
);
const protectedOutput = await mkdtemp(join(tmpdir(), "fcx-existing-capture-"));
try {
    await writeFile(join(protectedOutput, "results.json"), JSON.stringify({
        schemaVersion: "1",
        question: "同一个问题",
        platforms: [
            { platform: "dknowc-chat", label: "深知晓", status: "success" },
            { platform: "doubao", label: "豆包", status: "failed" }
        ]
    }), "utf8");
    await assert.rejects(
        () => ensureFreshCaptureOutput(protectedOutput, "同一个问题"),
        /拒绝重新提交或覆盖/
    );
}
finally {
    await rm(protectedOutput, { recursive: true, force: true });
}
assert.equal(
    looksLikeLoginOnlyText("完整政策回答中要求考生登录北京教育考试院网站填报信息。".repeat(8)),
    false
);
assert.equal(isPdfReference("https://example.gov.cn/policy/source.PDF?download=1"), true);
assert.equal(
    normalizeUrl("https://example.gov.cn/policy.pdf?f_link_type=f_linkinlinenote&flow_extra=opaque&download=1"),
    "https://example.gov.cn/policy.pdf?download=1"
);
const answer = await waitForAnswer(
    {
        name: "slow-test",
        label: "慢响应测试",
        selectors: { answer: [".answer"] },
        completionStableMs: 180
    },
    page,
    10000,
    "",
    "广州无合同租房提取住房公积金每月最高多少？"
);
assert.equal(answer, "每人每月最高提取 1400 元。");

const doubaoGenerationStarted = Date.now();
const doubaoGenerationPage = {
    locator(selector) {
        return {
            last() {
                return this;
            },
            async count() {
                return selector === ".md-box-root" || selector === "#flow-end-msg-stop" ? 1 : 0;
            },
            async isVisible() {
                return selector === "#flow-end-msg-stop";
            },
            async innerText() {
                return "";
            },
            async evaluate() {
                const elapsed = Date.now() - doubaoGenerationStarted;
                if (elapsed < 110) {
                    return "已定位政策材料";
                }
                return "经核对，企业和人员需满足现行政策条件，补贴标准以实际缴纳的单位部分为准。";
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 10)));
    }
};
const doubaoCompletedAnswer = await waitForAnswer(
    {
        name: "doubao",
        label: "豆包",
        selectors: { answer: [".md-box-root"] },
        completionStableMs: 20
    },
    doubaoGenerationPage,
    1000,
    "",
    "深圳小微企业社保补贴是什么？",
    {
        streamMonitor: {
            snapshot() {
                const elapsed = Date.now() - doubaoGenerationStarted;
                return {
                    started: elapsed >= 50,
                    completed: elapsed >= 140,
                    failed: false
                };
            }
        }
    }
);
assert.match(doubaoCompletedAnswer, /^经核对/);

const dknowStarted = Date.now();
const dknowPage = {
    locator(selector) {
        return {
            last() {
                return this;
            },
            async count() {
                return selector === ".chat-loading, .stopChat" ? 1 : 0;
            },
            async isVisible() {
                if (selector === ".chat-loading, .stopChat") {
                    return Date.now() - dknowStarted < 180;
                }
                return false;
            },
            async innerText() {
                return "";
            },
            async evaluateAll(callback) {
                const text = Date.now() - dknowStarted < 180
                    ? "工业互联网平台、MES"
                    : "工业互联网平台、MES/ERP系统升级完整测算与最终汇总。";
                return callback([{ innerText: text }]);
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 20)));
    }
};
const completeDknowAnswer = await waitForAnswer(
    {
        name: "dknowc-chat",
        label: "深知晓",
        selectors: {},
        completionStableMs: 40
    },
    dknowPage,
    3000,
    "",
    "复杂政策测算问题"
);
assert.equal(
    completeDknowAnswer,
    "工业互联网平台、MES/ERP系统升级完整测算与最终汇总。"
);

const deepProgressStarted = Date.now();
const deepProgressPage = {
    locator(selector) {
        return {
            last() {
                return this;
            },
            async count() {
                return 1;
            },
            async isVisible() {
                if (selector.includes("load-deep-search")) {
                    return Date.now() - deepProgressStarted < 180;
                }
                return false;
            },
            async innerText() {
                return "";
            },
            async evaluateAll(callback) {
                const text = Date.now() - deepProgressStarted < 180
                    ? "[查询]现行个人所得税法第六条 居民个人综合所得基本减除费用每月5000元"
                    : "深度研究结论：居民个人综合所得基本减除费用为每年六万元，即每月5000元。";
                return callback([{ innerText: text }]);
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 20)));
    }
};
const completeDeepResearchAnswer = await waitForAnswer(
    {
        name: "dknowc-deep-research",
        label: "深知晓（深度溯源）",
        selectors: {},
        completionStableMs: 40
    },
    deepProgressPage,
    3000,
    "",
    "复杂政策研究问题"
);
assert.equal(
    completeDeepResearchAnswer,
    "深度研究结论：居民个人综合所得基本减除费用为每年六万元，即每月5000元。"
);

let attempts = 0;
const retried = await captureWithRetries(
    { name: "retry-test", label: "重采测试", url: "https://example.invalid" },
    {
        timeoutMs: 1000,
        retryCount: 2,
        retryDelayMs: 0
    },
    async (config) => {
        attempts += 1;
        return attempts < 3
            ? {
                platform: config.name,
                label: config.label,
                url: config.url,
                status: "failed",
                answerMarkdown: "",
                references: [],
                error: "尚未采集完成"
            }
            : {
                platform: config.name,
                label: config.label,
                url: config.url,
                status: "success",
                answerMarkdown: "完整回答",
                references: []
            };
    },
    async () => undefined
);
assert.equal(attempts, 3);
assert.equal(retried.status, "success");

let pairedAttempts = 0;
const paired = await captureWithRetries(
    { name: "dknowc-chat", label: "深知晓", url: "https://yun.dknowc.cn/wlcb/szx/#/" },
    { timeoutMs: 1000, retryCount: 1, retryDelayMs: 0 },
    async (config) => {
        pairedAttempts += 1;
        return {
            platform: config.name,
            label: config.label,
            url: config.url,
            status: "success",
            answerMarkdown: "普通回答",
            references: [],
            captureLifecycle: {
                submissionAttempted: true,
                submissionConfirmed: true,
                answerObserved: true,
                resubmissionAllowed: false
            },
            companionResult: {
                platform: "dknowc-deep-research",
                label: "深知晓（深度溯源）",
                url: config.url,
                status: "failed",
                answerMarkdown: "",
                references: [],
                error: "深度溯源尚未完成"
            }
        };
    },
    async () => undefined
);
assert.equal(pairedAttempts, 1);
assert.equal(paired.status, "success");
assert.equal(paired.companionResult.status, "failed");

let postSubmissionAttempts = 0;
const postSubmissionFailure = await captureWithRetries(
    { name: "post-submit-test", label: "已提交恢复测试", url: "https://example.invalid" },
    { timeoutMs: 1000, retryCount: 2, retryDelayMs: 0 },
    async (config) => {
        postSubmissionAttempts += 1;
        return {
            platform: config.name,
            label: config.label,
            url: config.url,
            status: "failed",
            answerMarkdown: "",
            references: [],
            error: "回答选择器失效",
            captureLifecycle: {
                submissionAttempted: true,
                submissionCount: 1,
                submissionConfirmed: true,
                answerObserved: false,
                resubmissionAllowed: false
            }
        };
    },
    async () => undefined
);
assert.equal(postSubmissionAttempts, 1);
assert.equal(postSubmissionFailure.captureLifecycle.submissionCount, 1);

const replayQuestion = "页面关闭后必须重新提交的原问题";
let replayAttempts = 0;
const replayed = await captureWithRetries(
    { name: "page-replay-test", label: "页面恢复测试", url: "https://example.invalid" },
    { question: replayQuestion, timeoutMs: 1000, retryCount: 1, retryDelayMs: 0 },
    async (config, options) => {
        replayAttempts += 1;
        assert.equal(options.question, replayQuestion);
        return replayAttempts === 1
            ? { platform: config.name, label: config.label, url: config.url, status: "failed", answerMarkdown: "", references: [], error: "采集页面已关闭；将重开页面并自动重放原问题。" }
            : { platform: config.name, label: config.label, url: config.url, status: "success", answerMarkdown: `已回答：${options.question}`, references: [] };
    },
    async () => undefined
);
assert.equal(replayAttempts, 2);
assert.equal(replayed.answerMarkdown.includes(replayQuestion), true);

let manualGateAttempts = 0;
let manualGateWaits = 0;
const manualGateResult = await captureWithRetries(
    { name: "manual-gate-test", label: "人工门禁测试", url: "https://example.invalid" },
    {
        timeoutMs: 1000,
        retryCount: 2,
        retryDelayMs: 0
    },
    async (config) => {
        manualGateAttempts += 1;
        return {
            platform: config.name,
            label: config.label,
            url: config.url,
            status: "verification_required",
            answerMarkdown: "",
            references: [],
            error: "需要人工验证"
        };
    },
    async () => {
        manualGateWaits += 1;
    }
);
assert.equal(manualGateAttempts, 1);
assert.equal(manualGateWaits, 0);
assert.equal(manualGateResult.status, "verification_required");

const promptQuestion = "如何参加北京高考？";
let promptValue = promptQuestion;
let promptBody = "";
let promptAnswer = "";
const promptPage = {
    locator(selector) {
        return {
            last() {
                return this;
            },
            async count() {
                return selector === ".answer" && promptAnswer ? 1 : 0;
            },
            async isVisible() {
                return false;
            },
            async innerText() {
                if (selector === "body") {
                    return promptBody;
                }
                return promptAnswer;
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 10)));
    }
};
const promptInput = {
    async evaluate() {
        return promptValue;
    }
};
const promptConfig = {
    name: "prompt-test",
    label: "发送确认测试",
    selectors: { answer: [".answer"] }
};
assert.equal(
    await confirmPromptSubmission(promptConfig, promptPage, promptInput, promptQuestion, "", 40),
    "unconfirmed"
);
setTimeout(() => {
    promptValue = "";
}, 20);
assert.equal(
    await confirmPromptSubmission(promptConfig, promptPage, promptInput, promptQuestion, "", 200),
    "submitted"
);
promptValue = promptQuestion;
promptBody = "请完成人机验证";
assert.equal(
    await confirmPromptSubmission(promptConfig, promptPage, promptInput, promptQuestion, "", 100),
    "verification_required"
);
promptBody = "亲，请拖动下方滑块完成验证，通过验证以确保正常访问";
assert.equal(
    await confirmPromptSubmission(promptConfig, promptPage, promptInput, promptQuestion, "", 100),
    "verification_required"
);

let sendClicks = 0;
let enterPresses = 0;
const singleActionInput = {
    async evaluate() { return promptQuestion; }
};
const singleActionPage = {
    locator(selector) {
        return {
            last() { return this; },
            nth() { return this; },
            async count() { return selector === "#send" ? 1 : 0; },
            async isVisible() { return selector === "#send"; },
            async click() { sendClicks += 1; },
            async innerText() { return ""; }
        };
    },
    keyboard: { async press() { enterPresses += 1; } },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 5)));
    }
};
const singleActionLifecycle = { submissionCount: 0 };
assert.equal(await submitPromptAndConfirm(
    { name: "single-action", label: "单次提交", selectors: { send: ["#send"], answer: ["#missing"] } },
    singleActionPage,
    singleActionInput,
    promptQuestion,
    "",
    { submissionTimeoutMs: 20, captureLifecycle: singleActionLifecycle }
), "unconfirmed");
assert.equal(sendClicks, 1);
assert.equal(enterPresses, 0);
assert.equal(singleActionLifecycle.submissionCount, 1);

const lateGateStarted = Date.now();
const lateGatePage = {
    locator(selector) {
        return {
            last() {
                return this;
            },
            async count() {
                return selector === ".answer" ? 1 : 0;
            },
            async isVisible() {
                return false;
            },
            async innerText() {
                if (selector === "body") {
                    return Date.now() - lateGateStarted < 50 ? "请完成人机验证" : "";
                }
                return Date.now() - lateGateStarted < 80 ? "" : "北京高考报名条件完整回答";
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 10)));
    }
};
const answerAfterLateGate = await waitForAnswer(
    {
        name: "late-gate-test",
        label: "生成期验证测试",
        selectors: { answer: [".answer"] },
        completionStableMs: 40
    },
    lateGatePage,
    1000,
    "",
    promptQuestion,
    {
        interactive: true,
        verificationTimeoutMs: 500
    }
);
assert.equal(answerAfterLateGate, "北京高考报名条件完整回答");

const answeredDespiteExpiredSessionPage = {
    locator(selector) {
        const isAnswer = selector === ".answer";
        const isLoginSelector = selector.includes("登录") || selector.includes("login");
        return {
            last() {
                return this;
            },
            async count() {
                return isAnswer || isLoginSelector ? 1 : 0;
            },
            nth() {
                return this;
            },
            async isVisible() {
                return isLoginSelector;
            },
            async innerText() {
                if (selector === "body") {
                    return "会话过期，请重新登录";
                }
                if (isAnswer) {
                    return "北京市高考报名资格、网上申请、填报缴费和现场确认的完整回答。".repeat(5);
                }
                return "";
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 10)));
    }
};
const recoveredAfterSessionExpiry = await waitForAnswer(
    {
        name: "expired-after-answer-test",
        label: "回答完成后登录失效测试",
        requiresLogin: true,
        selectors: {
            answer: [".answer"],
            loginGate: ["button:has-text('登录')"]
        },
        completionStableMs: 40
    },
    answeredDespiteExpiredSessionPage,
    1000,
    "",
    promptQuestion,
    {
        interactive: false,
        verificationTimeoutMs: 100
    }
);
assert.match(recoveredAfterSessionExpiry, /现场确认/);

let loggedIn = false;
const loggedOutPageWithInput = {
    locator(selector) {
        const isLoginSelector = selector.includes("登录") || selector.includes("login");
        return {
            async count() {
                return isLoginSelector ? 1 : selector.includes("textarea") ? 1 : 0;
            },
            nth() {
                return this;
            },
            async isVisible() {
                return isLoginSelector ? !loggedIn : true;
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 20)));
    }
};
const doubaoConfig = {
    name: "doubao",
    label: "豆包",
    requiresLogin: true,
    selectors: {
        loginGate: ["[class*='login-btn-header']", "button:has-text('登录')"],
        input: ["textarea"]
    }
};
assert.equal(await authenticationRequired(loggedOutPageWithInput, doubaoConfig), true);
setTimeout(() => {
    loggedIn = true;
}, 60);
assert.equal(await waitForAuthentication(loggedOutPageWithInput, doubaoConfig, 5000), true);

let transientAuthCheck = 0;
const transientReloadPage = {
    locator(selector) {
        const isLoginSelector = selector.includes("登录") || selector.includes("login");
        return {
            async count() {
                return isLoginSelector ? 1 : 0;
            },
            nth() {
                return this;
            },
            async isVisible() {
                if (!isLoginSelector) {
                    return false;
                }
                transientAuthCheck += 1;
                return transientAuthCheck !== 2;
            },
            async innerText() {
                return "";
            }
        };
    },
    async waitForTimeout(milliseconds) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(milliseconds, 10)));
    }
};
assert.equal(
    await waitForAuthentication(transientReloadPage, doubaoConfig, 120),
    false
);

const yuanbaoConfig = builtInPlatforms.find((platform) => platform.name === "yuanbao");
assert.equal(yuanbaoConfig?.requiresLogin, true);
const yuanbaoLoginOverlayPage = {
    locator(selector) {
        const isLoginOverlay = selector === ".hyc-login-v2";
        return {
            async count() {
                return isLoginOverlay ? 1 : 0;
            },
            nth() {
                return this;
            },
            async isVisible() {
                return isLoginOverlay;
            },
            async innerText() {
                return selector === "body" ? "请登录后输入内容" : "";
            }
        };
    }
};
assert.equal(
    await authenticationRequired(yuanbaoLoginOverlayPage, yuanbaoConfig),
    true
);

const deepseekConfig = builtInPlatforms.find((platform) => platform.name === "deepseek");
assert.equal(deepseekConfig?.requiresLogin, true);
const deepseekLoginPage = {
    locator(selector) {
        const isLoginForm = selector === ".ds-sign-in-form__main";
        return {
            async count() {
                return isLoginForm ? 1 : 0;
            },
            nth() {
                return this;
            },
            async isVisible() {
                return isLoginForm;
            },
            async innerText() {
                return selector === "body" ? "请输入手机号 请输入验证码 微信扫码登录" : "";
            }
        };
    }
};
assert.equal(
    await authenticationRequired(deepseekLoginPage, deepseekConfig),
    true
);

const expiredSessionPage = {
    locator(selector) {
        return {
            async count() {
                return 0;
            },
            nth() {
                return this;
            },
            async isVisible() {
                return false;
            },
            async innerText() {
                return selector === "body" ? "会话过期，请重新登录" : "";
            }
        };
    }
};
assert.equal(await authenticationRequired(expiredSessionPage, doubaoConfig), true);

let doubaoSourceSelector = "";
const doubaoSourcePage = {
    locator(selector) {
        assert.equal(selector, ".md-box-root");
        return {
            last() {
                return {
                    locator(sourceSelector) {
                        doubaoSourceSelector = sourceSelector;
                        return {
                            async evaluateAll(callback) {
                                return callback([
                                    { textContent: "广州住房公积金管理中心" },
                                    { textContent: " 广州住房公积金管理中心 " },
                                    { textContent: "广州市人民政府" }
                                ]);
                            }
                        };
                    }
                };
            }
        };
    }
};
const sourceMentions = await extractDoubaoSourceMentions(doubaoSourcePage);
assert.match(doubaoSourceSelector, /container-sWvQla/);
assert.deepEqual(sourceMentions, [
    { label: "广州住房公积金管理中心", marker: "1", occurrenceCount: 2 },
    { label: "广州市人民政府", marker: "2", occurrenceCount: 1 }
]);
if (process.env.FACT_CHECK_X_ASSERTIONS_OUTPUT) {
    const { writeFile } = await import("node:fs/promises");
    await writeFile(process.env.FACT_CHECK_X_ASSERTIONS_OUTPUT, JSON.stringify({
        schemaVersion: "fact-check-x/test-assertions@1",
        actualAssertionIds: [
            "browser.question_replayed",
            "browser.retry_submitted",
            "verification.qianwen_slider_detected",
            "capture.doubao_intermediate_not_complete",
            "capture.existing_results_no_resubmit"
        ]
    }));
}
console.log("PASS 采集等待完整回答");
