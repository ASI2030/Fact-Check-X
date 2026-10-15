import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from fact_check_x import validate_capture_results, PipelineError
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'results.json'
    data={'schemaVersion':'1','question':'深圳高企奖励','platforms':[{'platform':'doubao','status':'success',
        'answerMarkdown':'已将该调研任务交给组织者 Agent 后台执行，完成后会把结果整理给你。期间你可以继续提问。'}]}
    path.write_text(json.dumps(data))
    try: validate_capture_results(path)
    except PipelineError as e: assert '后台任务受理通知' in str(e)
    else: raise AssertionError('interim answer was admitted to comparison')
    data['platforms'][0]['answerMarkdown']='调研已完成。以下是结果：各项目按适用年度、对象分别申报。'
    path.write_text(json.dumps(data))
    assert validate_capture_results(path)
print('PASS pipeline rejects falsely successful background acknowledgement')
