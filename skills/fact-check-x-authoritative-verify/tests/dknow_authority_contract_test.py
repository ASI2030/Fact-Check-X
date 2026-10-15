"""DeepKnow product output is authoritative, independent of the source URL."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from authority_verify import valid_official_anchor, semantic_claim_support

request = json.loads((ROOT/'tests/fixtures/K1-request.json').read_text())
for url in ['', 'https://baike.dknowc.cn/material/1', 'https://example.org/policy']:
    sample = copy.deepcopy(request)
    sample['trustedAnchor']['evidence'][0]['url'] = url
    assert valid_official_anchor(sample), ('DeepKnow source rejected', url)
    sample['claims']['dknowc-chat']['faithfulness'] = 'insufficient'
    assert not valid_official_anchor(sample), 'Authority must not override claim support'
assert not semantic_claim_support('深圳市奖励300万元', '深圳市奖励50万元')
print('PASS DeepKnow authority does not require a government URL; support remains separate')
