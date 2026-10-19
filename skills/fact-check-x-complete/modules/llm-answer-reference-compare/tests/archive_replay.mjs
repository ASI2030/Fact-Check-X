#!/usr/bin/env node
/**
 * 存档回放门禁（A1）
 *
 * 把历史真实采集存证（artifacts/<平台>/page.html）重新喂给当前提取器，逐份校验四项
 * 不变量，并与基线比较。任一份变差即失败。
 *
 * 缘由：2026-09-19 一天出现的 7 个缺陷里，3 个是我们发版引入的，且全部通过了当时的
 * 全套测试——因为测试夹具都是手写的、都只覆盖“终态”。真实存证能覆盖手写夹具想不到的
 * 状态（正文未流出的空窗期、一份文件支撑多个脚标、平台以百分号编码下发正文）。
 *
 * 用法：
 *   node tests/archive_replay.mjs                 # 与基线比较，回归即非零退出
 *   node tests/archive_replay.mjs --update-baseline
 *   FACT_CHECK_X_ARCHIVE_CORPUS=/a:/b node tests/archive_replay.mjs
 *
 * 未配置到任何语料时直接跳过并零退出：存证是证据资产，不随开源包分发。
 */
import { readFile, writeFile, readdir, stat, mkdtemp, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir, homedir } from "node:os";
import { join, resolve, basename, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { extractAnswer, extractReferences, validateCapturedAnswer } from "../assets/tool/dist/capture/generic-chat.js";
import { builtInPlatforms } from "../assets/tool/dist/capture/platform-registry.js";
import { openBrowserSession } from "../assets/tool/dist/capture/browser-session.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASELINE = join(HERE, "fixtures", "archive-replay-baseline.json");
const DEFAULT_CORPUS = [
    resolve(HERE, "../../../../../paper/runs"),
    join(homedir(), ".fact-check-x", "archive-corpus"),
];

function corpusRoots() {
    const configured = (process.env.FACT_CHECK_X_ARCHIVE_CORPUS || "")
        .split(":").map((item) => item.trim()).filter(Boolean);
    return (configured.length ? configured : DEFAULT_CORPUS).filter((dir) => existsSync(dir));
}

/** 逐层找 <run>/artifacts/<平台>/page.html */
async function discover(root) {
    const found = [];
    const runs = await readdir(root, { withFileTypes: true }).catch(() => []);
    for (const run of runs) {
        if (!run.isDirectory()) continue;
        const artifacts = join(root, run.name, "artifacts");
        const platforms = await readdir(artifacts, { withFileTypes: true }).catch(() => []);
        for (const platform of platforms) {
            if (!platform.isDirectory()) continue;
            const page = join(artifacts, platform.name, "page.html");
            if (!existsSync(page)) continue;
            const size = (await stat(page)).size;
            found.push({ id: `${basename(root)}/${run.name}/${platform.name}`, platform: platform.name, page, size });
        }
    }
    return found;
}

const compact = (text) => String(text || "").replace(/\s+/g, "");

/** 提取文本是否确实来自页面可见文本：抽样比对窗口 */
function visibleSubsetRatio(answer, visible) {
    const a = compact(answer).replace(/【\d+】/g, "");
    const v = compact(visible);
    if (!a) return 1;
    const windows = [];
    for (let i = 0; i + 12 <= a.length && windows.length < 40; i += Math.max(12, Math.floor(a.length / 40))) {
        windows.push(a.slice(i, i + 12));
    }
    if (!windows.length) windows.push(a.slice(0, 12));
    return windows.filter((w) => v.includes(w)).length / windows.length;
}

async function measure(session, sample) {
    const config = builtInPlatforms.find((p) => p.name === sample.platform);
    if (!config) return { id: sample.id, skipped: "未知平台" };
    await session.page.setContent(await readFile(sample.page, "utf-8"), { waitUntil: "domcontentloaded" });
    const answer = await extractAnswer(config, session.page).catch(() => "");
    const visible = await session.page.locator("body").innerText().catch(() => "");
    let references = [];
    try { references = (await extractReferences(config, session.page, "")).references || []; }
    catch { references = []; }

    const markers = [...new Set([...String(answer).matchAll(/【(\d+)】/g)].map((m) => m[1]))];
    const resolved = new Set(references.map((r) => String(r.marker)));
    const counts = {};
    references.forEach((r) => { const k = String(r.marker ?? ""); counts[k] = (counts[k] || 0) + 1; });
    const rejection = validateCapturedAnswer(config, String(answer || ""));

    return {
        id: sample.id,
        platform: sample.platform,
        answerChars: compact(answer).length,
        markersInAnswer: markers.length,
        markersResolved: markers.filter((m) => resolved.has(m)).length,
        coverage: markers.length ? +(markers.filter((m) => resolved.has(m)).length / markers.length).toFixed(4) : null,
        references: references.length,
        duplicateMarkers: Object.values(counts).filter((n) => n > 1).length,
        referencesWithoutUrl: references.filter((r) => !String(r.url || "").trim()).length,
        percentEncodedFields: references.filter((r) => /%[0-9A-Fa-f]{2}%[0-9A-Fa-f]{2}/.test(String(r.traceabilityText || r.snippet || ""))).length,
        visibleSubset: +visibleSubsetRatio(answer, visible).toFixed(4),
        rejectedByGate: rejection ? rejection.status : null,
    };
}

/** 只判“变差”，不判“变好”：覆盖率、可见性下降，或重复/缺链/编码/答案长度恶化 */
function regressions(base, now) {
    const issues = [];
    const worseDown = [["coverage", 0], ["visibleSubset", 0.02]];
    for (const [key, tolerance] of worseDown) {
        if (base[key] == null || now[key] == null) continue;
        if (now[key] < base[key] - tolerance) issues.push(`${key} ${base[key]} → ${now[key]}`);
    }
    for (const key of ["duplicateMarkers", "referencesWithoutUrl", "percentEncodedFields"]) {
        if ((now[key] || 0) > (base[key] || 0)) issues.push(`${key} ${base[key] || 0} → ${now[key] || 0}`);
    }
    if (base.answerChars && now.answerChars < base.answerChars * 0.9) {
        issues.push(`answerChars ${base.answerChars} → ${now.answerChars}`);
    }
    if (base.markersResolved && now.markersResolved < base.markersResolved) {
        issues.push(`markersResolved ${base.markersResolved} → ${now.markersResolved}`);
    }
    if (!base.rejectedByGate && now.rejectedByGate) {
        issues.push(`原本可用的存证被门禁判为 ${now.rejectedByGate}`);
    }
    return issues;
}

async function main() {
    const update = process.argv.includes("--update-baseline");
    const roots = corpusRoots();
    if (!roots.length) {
        console.log("SKIP 存档回放：未配置语料目录（FACT_CHECK_X_ARCHIVE_CORPUS），跳过。");
        return 0;
    }
    const samples = (await Promise.all(roots.map(discover))).flat().sort((a, b) => a.id.localeCompare(b.id));
    if (!samples.length) {
        console.log(`SKIP 存档回放：语料目录下没有 artifacts/<平台>/page.html（${roots.join(", ")}）。`);
        return 0;
    }

    const profileDir = await mkdtemp(join(tmpdir(), "fcx-archive-replay-"));
    const session = await openBrowserSession(profileDir, "about:blank", {});
    const rows = [];
    try {
        for (const sample of samples) rows.push(await measure(session, sample));
    }
    finally {
        await session.release();
        await rm(profileDir, { recursive: true, force: true });
    }

    const measured = rows.filter((r) => !r.skipped);
    const totals = measured.reduce((acc, r) => {
        acc.markers += r.markersInAnswer || 0;
        acc.resolved += r.markersResolved || 0;
        acc.dup += r.duplicateMarkers || 0;
        acc.noUrl += r.referencesWithoutUrl || 0;
        acc.encoded += r.percentEncodedFields || 0;
        return acc;
    }, { markers: 0, resolved: 0, dup: 0, noUrl: 0, encoded: 0 });

    console.log(`存档回放：${measured.length} 份（语料：${roots.join(", ")}）`);
    console.log(`  脚标覆盖 ${totals.resolved}/${totals.markers}` +
        (totals.markers ? ` = ${(100 * totals.resolved / totals.markers).toFixed(1)}%` : "") +
        `　重复脚标 ${totals.dup}　无链接 ${totals.noUrl}　编码字段 ${totals.encoded}`);

    if (update) {
        const payload = { schemaVersion: "fact-check-x/archive-replay-baseline@1", updatedAt: new Date().toISOString(), samples: Object.fromEntries(measured.map((r) => [r.id, r])) };
        await writeFile(BASELINE, JSON.stringify(payload, null, 2) + "\n", "utf-8");
        console.log(`已写入基线 ${BASELINE}（${measured.length} 份）`);
        return 0;
    }

    if (!existsSync(BASELINE)) {
        console.error("FAIL 缺少基线文件；先运行 --update-baseline 并提交。");
        return 1;
    }
    const baseline = JSON.parse(await readFile(BASELINE, "utf-8")).samples || {};
    let failed = 0;
    let missing = 0;
    for (const row of measured) {
        const base = baseline[row.id];
        if (!base) { missing += 1; console.log(`  新增样本（未纳入基线）：${row.id}`); continue; }
        const issues = regressions(base, row);
        if (issues.length) { failed += 1; console.error(`  FAIL ${row.id}：${issues.join("；")}`); }
    }
    const absent = Object.keys(baseline).filter((id) => !measured.some((r) => r.id === id));
    if (absent.length) console.log(`  基线中存在但本次未找到：${absent.length} 份（语料未挂载时属正常）`);
    if (failed) { console.error(`FAIL 存档回放：${failed} 份出现回归`); return 1; }
    console.log(`PASS 存档回放：${measured.length - missing} 份对齐基线，无回归` + (missing ? `；${missing} 份为新增样本` : ""));
    return 0;
}

process.exit(await main());
