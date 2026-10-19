import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resumeExistingCapture } from "../assets/tool/dist/capture/resume.js";
import { resumeCommand } from "../assets/tool/dist/cli.js";
import { parseRunResult } from "../assets/tool/dist/schema/result.js";
import { resolveVisibleBrowserExecutable } from "../assets/tool/dist/capture/browser-session.js";

let submissions = 0;
const question = "恢复原会话并保留引用";
const server = createServer((req, res) => {
    if (req.method === "POST" || req.url === "/submit") submissions++;
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    if (req.url === "/source") return res.end("<article>官方条款：符合条件的家庭年收入限额为54000元。</article>");
    res.end(`<main><p id="question">${req.url === "/wrong" ? "另一道题" : question}</p>
      <textarea></textarea><button onclick="fetch('/submit',{method:'POST'})">发送</button>
      <article id="answer"></article></main><script>
      setTimeout(()=>document.querySelector('#answer').innerHTML='这是原会话完整答案，符合条件的家庭年收入限额为54000元。<a href="/source">官方条款</a>', 300);
      </script>`);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const dir = await mkdtemp(join(tmpdir(), "fcx-resume-"));
const config = { name: "resume-test", label: "恢复测试", url: origin, profile: `resume-test-${Date.now()}`,
    requiresLogin: false, completionStableMs: 100, selectors: { question: "#question", answer: ["#answer"] } };
const prior = { platform: config.name, label: config.label, url: origin, status: "timeout", answerMarkdown: "", references: [], error: "timeout",
    captureLifecycle: { submissionAttempted: true, submissionConfirmed: true, submissionCount: 1 } };
try {
    const options = { question, conversationUrl: `${origin}/conversation/1`, timeoutMs: 7000, loginTimeoutMs: 1000,
        headed: false, interactive: false, executablePath: resolveVisibleBrowserExecutable(), outDir: join(dir, "browser") };
    const restored = await resumeExistingCapture(config, prior, options);
    assert.equal(restored.status, "success", restored.error);
    assert.match(restored.answerMarkdown, /54000/);
    assert.equal(restored.references.length, 1);
    assert.equal(restored.captureLifecycle.submissionCount, 1);
    assert.equal(restored.captureLifecycle.recoverySubmissionCount, 0);
    assert.equal(submissions, 0);
    await assert.rejects(resumeExistingCapture(config, prior, { ...options, conversationUrl: origin }), /平台首页/);
    await assert.rejects(resumeExistingCapture(config, prior, { ...options, conversationUrl: "https://example.com/wrong" }), /属于原平台/);
    const wrong = await resumeExistingCapture(config, prior, { ...options, conversationUrl: `${origin}/wrong`, outDir: join(dir, "wrong") });
    assert.equal(wrong.status, "failed");
    assert.match(wrong.error, /不一致/);
    assert.equal(submissions, 0);

    const failed = { ...prior, platform: "doubao", url: "https://www.doubao.com/chat/", label: "豆包" };
    const other = { ...failed, platform: "deepseek", label: "DeepSeek", error: "not ready" };
    const run = { schemaVersion: "1", question, createdAt: new Date().toISOString(), platforms: [failed, other] };
    const input = join(dir, "results.json");
    await writeFile(input, JSON.stringify(run));
    const bytes = await readFile(input);
    assert.equal(parseRunResult(run).platforms[0].captureLifecycle.submissionCount, 1);
    const out = join(dir, "merged");
    await assert.rejects(resumeCommand({ input, platform: "doubao", out, conversationUrl: "https://www.doubao.com/chat/old" },
        async () => ({ ...restored, platform: "doubao", label: "豆包" })), /尚未完成/);
    const merged = JSON.parse(await readFile(join(out, "results.json")));
    assert.deepEqual(merged.platforms.map(x => x.platform), ["doubao", "deepseek"]);
    assert.deepEqual(merged.platforms[1], parseRunResult(run).platforms[1]);
    assert.deepEqual(await readFile(input), bytes);
    assert.equal(merged.recovery.submissionCount, 0);
    await assert.rejects(resumeCommand({ input, platform: "doubao", out }), /空目录/);
    await assert.rejects(resumeCommand({ input, platform: "yuanbao", out: join(dir, "absent") }), /失败平台/);
    assert.equal(submissions, 0);
    console.log("PASS browser resume, delayed answer, original question identity, zero submissions, immutable input, platform set and lifecycle persistence");
} finally {
    server.close();
    await rm(dir, { recursive: true, force: true });
}
