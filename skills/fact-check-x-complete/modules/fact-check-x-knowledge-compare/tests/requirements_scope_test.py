import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from knowledge_compare import normalize_role
question='国家高新技术企业认定需要满足哪些条件？深圳市及各区有哪些奖励或资助，分别适用什么对象和年度？'
for description in ['科技人员比例不低于10%','高新收入占比不低于60%','申请前一年重大事故与严重环境违法']:
    for role in ['direct','reference']:
        assert normalize_role(question,description,role)[0]=='direct',description
assert normalize_role(question,'办理窗口咨询电话','direct')[0]=='reference'
assert normalize_role('深圳市级高企认定有什么奖励','福田区奖励最高10万元','direct')[0]=='reference'
assert normalize_role(question,'其他地区示例：北京科技人员不低于10%','direct')[0]=='reference'
print('PASS requested legal requirements retained; contact/other-region extras remain supplemental')
