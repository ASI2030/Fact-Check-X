import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from '../assets/tool/node_modules/playwright/index.mjs';
import { extractDknowcAnswer, readDknowcStandaloneReportState } from '../assets/tool/dist/capture/generic-chat.js';

const browser = await chromium.launch({ headless: true });
try {
    const context = await browser.newContext({ javaScriptEnabled: false, offline: true, serviceWorkers: 'block' });
    await context.route('**/*', route => route.abort());
    const page = await context.newPage();
    await page.setContent('<div id="steps" class="done"></div><span id="st-gen">已完成</span><div id="report"><p>第一条完整答案<sup data-cite="3">3</sup><sup data-cite="4">4</sup></p><p>第二条完整答案<sup>77</sup><sup>78</sup></p><p>真实单个编号<sup>34</sup></p><p style="display:none">隐藏<sup>99</sup></p></div>');
    const state = await readDknowcStandaloneReportState(page);
    assert.ok(state.done && state.substantive);
    assert.match(state.reportText, /答案【3】【4】\n/);
    assert.match(state.reportText, /答案【77】【78】/);
    assert.match(state.reportText, /编号【34】/);
    assert.ok(!state.reportText.includes('99'));
    await page.locator('#steps').evaluate(node => { node.classList.remove('done'); });
    assert.equal((await readDknowcStandaloneReportState(page)).done, false);
    if (process.argv[2]) {
        await page.setContent(await readFile(process.argv[2], 'utf8'), { waitUntil: 'domcontentloaded' });
        const answer = await extractDknowcAnswer(page);
        assert.match(answer, /300万元【3】【4】/);
        assert.match(answer, /300万元【77】【78】/);
        assert.ok(!/300万元(?:34|7778)。/.test(answer));
        if (process.argv[3]) await writeFile(process.argv[3], answer);
    }
    console.log('PASS standalone citation boundaries, hidden text, completion state and archived DOM');
} finally {
    await browser.close();
}
