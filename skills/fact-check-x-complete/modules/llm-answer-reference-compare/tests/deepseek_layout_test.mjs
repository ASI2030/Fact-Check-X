import assert from 'node:assert/strict';
import {chromium} from '../assets/tool/node_modules/@playwright/test/index.mjs';
import {extractAnswer} from '../assets/tool/dist/capture/generic-chat.js';
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage();
 await page.setContent('<div class="ds-assistant-message-main-content"><p>依据<a href="https://example.org">政策原文</a><a href="https://example.org/cite"><span class="ds-markdown-cite">1</span></a></p><ul><li>条件甲</li><li>条件乙</li></ul><table><tr><td>深圳</td><td>50万元</td></tr><tr><td>北京</td><td>20万元</td></tr></table></div>');
 const text=await extractAnswer({name:'deepseek'},page);
 assert.ok(text.includes('政策原文[1]'));
 assert.ok(text.includes('条件甲\n'));
 assert.ok(text.includes('深圳\t50万元\n'));
 assert.ok(text.includes('北京\t20万元'));
 await page.setContent('<div class="md-box-root"><div class="container-enLQFx">最终结果</div><table><tr><td>福田</td><td>10万元</td></tr><tr><td>盐田</td><td>30万元</td></tr></table></div>');
 const doubao=await extractAnswer({name:'doubao'},page);
 assert.ok(doubao.includes('福田\t10万元\n'));
 assert.ok(doubao.includes('盐田\t30万元'));
 console.log('PASS DeepSeek paragraph/list/table/citation and anchor text');
}finally{await browser.close();}
