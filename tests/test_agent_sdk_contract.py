"""Use a separate SDK 3.13 interpreter; never import it into core Gradio."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from test_agent_runtime import runtime,FakeClient,turn,ROOT


def check(payload):
    interpreter=os.environ.get('CHUANHU_AGENT_TEST_PYTHON') or str(ROOT/'.agents-runtime'/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))
    sdk_path=os.environ.get('CHUANHU_AGENT_TEST_SDK_PATH')
    if not Path(interpreter).is_file():pytest.skip('Install the optional pinned runtime to check its SDK contract')
    environment=dict(os.environ)
    for name in list(environment):
        if any(part in name.upper() for part in ('API_KEY','SECRET','TOKEN','PASSWORD')) or name.upper().startswith('OPENAI_'):
            environment.pop(name,None)
    if sdk_path:environment['PYTHONPATH']=sdk_path
    else:environment.pop('PYTHONPATH',None)
    environment['PYTHONDONTWRITEBYTECODE']='1'
    return subprocess.run([interpreter,str(ROOT/'tests/agent_sdk_contract.py')],input=json.dumps(payload),
                          capture_output=True,text=True,cwd='/tmp',env=environment,timeout=30)


@pytest.mark.parametrize('tools',[False,True])
def test_entire_actual_create_payload_matches_sdk_and_serializes(tools):
    client=FakeClient([turn('created'),turn('completed')])
    runtime.run_task(client,'offline synthetic input','gpt-6-astra',instructions='offline instructions',
                     run_id='a'*32,allow_text_tool=tools)
    payload=client.payloads[0]
    result=check(payload)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['sdk_version']=='3.13.0' and report['serialized']==payload
    assert report['serialized']['environment']['network']=={'access':'disabled'}


def test_real_sdk_contract_rejects_the_previous_network_mode_field():
    client=FakeClient([turn('created'),turn('completed')]);runtime.run_task(client,'offline','gpt-6-astra')
    payload=copy.deepcopy(client.payloads[0]);payload['environment']['network']={'mode':'disabled'}
    result=check(payload)
    assert result.returncode!=0 and 'SDK' in result.stderr
