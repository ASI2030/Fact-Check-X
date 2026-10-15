import copy
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from authority_verify import acquire, finalize, SkillError
request=json.loads((ROOT/'tests/fixtures/K1-request.json').read_text())
anchor=request['trustedAnchor']
anchor['claimEvidenceMap']={'dknowc-chat':['A1']}
new=copy.deepcopy(anchor['evidence'][0])
new.update(id='A2',url='https://example.gov.cn/new',sourcePolicy='gov_cn_reference',excerpt='2026年每人每月最高提取额度为2000元。',body='2026年每人每月最高提取额度为2000元。')
anchor['evidence'].append(new)
evidence=acquire(request)
assessment=json.loads((ROOT/'tests/fixtures/K1-assessment.json').read_text())
assessment['authoritativeFinding']='2026年额度为每人每月2000元，旧口径不适用。'
assessment['verdicts']['dknowc-chat']={'verdict':'contradicted','reason':'原回答引用旧口径，但A2说明2026年为2000元。','evidenceIds':['A2']}
assessment['verdicts']['doubao']={'verdict':'supported','reason':'与A2当前年度一致。','evidenceIds':['A2']}
result=finalize(request,evidence,assessment)
assert result['verdicts']['dknowc-chat']['verdict']=='contradicted'
assert result['claims']['dknowc-chat']['faithfulness']=='supported'
assessment['verdicts']['dknowc-chat']['evidenceIds']=[]
try: finalize(request,evidence,assessment)
except SkillError: pass
else: raise AssertionError('unsupported override admitted')
print('PASS source faithfulness retained independently of fact adjudication')
