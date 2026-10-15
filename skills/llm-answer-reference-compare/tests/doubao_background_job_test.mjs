import assert from 'node:assert/strict';
import {looksLikeDoubaoInterimAnswer, validateCapturedAnswer} from '../assets/tool/dist/capture/generic-chat.js';
const interim = '已将该调研任务交给组织者 Agent 后台执行，会并行检索国家认定条件、深圳市级及各区（福田、南山、宝安、龙岗、龙华等十余个区）的最新奖励政策，并逐条核实官方来源与适用年度。完成后我会把认定条件、各区奖励金额、适用对象和政策年度的对比结果整理给你（含可视化表格）。期间你可以继续提问或布置其他任务。';
assert.equal(looksLikeDoubaoInterimAnswer(interim), true);
assert.equal(validateCapturedAnswer({name:'doubao'},interim).status, 'failed');
assert.equal(looksLikeDoubaoInterimAnswer('调研已完成。以下是结果：深圳市企业应按各项目申报条件申报，2023年公示不代表2026年仍然适用。'),false);
assert.equal(looksLikeDoubaoInterimAnswer('申报完成后，可在后台查询办理进度。企业须满足成立年限、知识产权等条件。'),false);
console.log('PASS real background-job acknowledgement rejected; completed answers preserved');
