"""Tool configuration, permissions and transient authentication, all offline."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from optional.agents import tools
from modules.agent_settings import load_settings, save_settings
from modules.agent_store import BindingStore, owner_identity

@pytest.fixture(autouse=True)
def control_dir(tmp_path, monkeypatch): monkeypatch.setattr(tools,'CONTROL_ROOT',tmp_path/'control')


def client_for(action, status='requires_action'):
    calls=[]
    client=SimpleNamespace(beta=SimpleNamespace(agents=SimpleNamespace(sessions=SimpleNamespace(
        retrieve=lambda session:{'id':session,'status':status,'required_actions':[action]},
        events=SimpleNamespace(create=lambda *a,**k:calls.append((a,k)))))))
    return client,calls


def origin_action(request_id='req_one',turn_id='turn_one'):
    return {'type':'computer_use_approval_request','turn_id':turn_id,'request_id':request_id,
            'request':{'type':'browser_origin_access','origin':'https://example.com','reason':'Read page'}}


def login_action(options=False):
    value=origin_action();value['request']={'type':'browser_authentication','credential_origin':'https://example.com',
        'fields':[{'id':'email','label':'Email','type':'text','required':True},{'id':'password','label':'Password','type':'password','required':True}], 'options':[]}
    if options:value['request']['options']=[{'id':'email_method','label':'Email only','field_ids':['email']}]
    return value


def test_default_builtins_and_distinct_network_search():
    config=tools.build_tool_config({})
    assert config['environment']=={'type':'openai_hosted','network':{'access':'enabled'},'desktop':{'enabled':True}}
    assert {x['type'] for x in config['tools']}=={'web_search','computer_use','programmatic_tool_calling'}
    assert not config['snapshot']['functions'] and not config['snapshot']['mcp_servers']
    config=tools.build_tool_config({'network':False})
    assert config['environment']['network']['access']=='disabled'
    assert next(x for x in config['tools'] if x['type']=='web_search')['mode']=='live'
    config=tools.build_tool_config({'web_search':False})
    assert config['environment']['network']['access']=='enabled' and 'web_search' not in {x['type'] for x in config['tools']}


def test_disabled_tools_not_discoverable_or_programmatically_exposed():
    config=tools.build_tool_config({'web_search':False,'computer_use':False,'programmatic_tool_calling':False,'functions':[]})
    assert config['tools']==[]
    config=tools.build_tool_config({'functions':['text_statistics']})
    assert next(x for x in config['tools'] if x['type']=='function')['defer_loading']
    assert {'tool_search','function'}<={x['type'] for x in config['tools']}


@pytest.mark.parametrize('settings',[{'code_execution':False},{'network':'true'},{'search_mode':'broken'},{'search_domains':['https://example.com/a']},{'functions':['unregistered']},{'unexpected':True}])
def test_invalid_settings_are_actionable(settings):
    with pytest.raises(tools.ToolConfigurationError):tools.validate_settings(settings)


def test_defaults_persist_disabled_across_instances(tmp_path):
    owner=owner_identity('alice');settings=tools.validate_settings({'network':False,'web_search':False})
    save_settings(tmp_path,owner,settings)
    assert load_settings(tmp_path,owner)==settings and load_settings(tmp_path,owner_identity('bob'))['network']
    assert (tmp_path/'agent_data'/owner/'settings.json').stat().st_mode & 0o777==0o600


@pytest.mark.parametrize('decision',['approve','deny','cancel'])
def test_origin_response_exact_request_and_same_session(decision):
    client,calls=client_for(origin_action())
    result=tools.submit_browser_response(client,'sess_one','turn_one','req_one',{'type':'browser_origin_access','decision':decision})
    assert result['accepted'] and len(calls)==1
    assert calls[0][0]==('sess_one',)
    event=calls[0][1]['events'][0]
    assert event=={'type':'agent.session.input.computer_use_approval_request_result','request_id':'req_one','response':{'type':'browser_origin_access','decision':decision}}


@pytest.mark.parametrize('change',[{'turn':'other'},{'request':'old'},{'status':'idle'},{'response':{'type':'browser_origin_access','decision':'approve','origin':'https://evil.example'}},{'response':{'type':'browser_authentication','action':'cancel'}}])
def test_stale_or_widened_approval_rejected(change):
    client,calls=client_for(origin_action(),change.get('status','requires_action'))
    with pytest.raises(tools.ToolConfigurationError):tools.submit_browser_response(client,'sess_one',change.get('turn','turn_one'),change.get('request','req_one'),change.get('response',{'type':'browser_origin_access','decision':'approve'}))
    assert not calls


def test_login_has_only_chosen_fields_and_never_chat_message():
    client,calls=client_for(login_action(True))
    response={'type':'browser_authentication','action':'submit','selected_option':'email_method','fields':[{'field_id':'email','value':'private@example.com'}]}
    tools.submit_browser_response(client,'sess_one','turn_one','req_one',response)
    event=calls[0][1]['events'][0]
    assert 'turn_id' not in event and event['type'].endswith('computer_use_approval_request_result')
    assert event['response']==response
    assert 'private@example.com' not in json.dumps(tools.pending_action_cards({'required_actions':[login_action(True)]},'turn_one'))


@pytest.mark.parametrize('response',[
    {'type':'browser_authentication','action':'submit','fields':[]},
    {'type':'browser_authentication','action':'submit','selected_option':'missing','fields':[]},
    {'type':'browser_authentication','action':'submit','selected_option':'email_method','fields':[{'field_id':'password','value':'secret'}]},
    {'type':'browser_authentication','action':'submit','selected_option':'email_method','fields':[{'field_id':'email','value':''}]},
    {'type':'browser_authentication','action':'cancel','fields':[{'field_id':'email','value':'secret'}]},
])
def test_invalid_login_payloads_do_not_submit(response):
    client,calls=client_for(login_action(True))
    with pytest.raises(tools.ToolConfigurationError):tools.submit_browser_response(client,'sess_one','turn_one','req_one',response)
    assert not calls


def test_login_cancellation_is_not_task_cancellation():
    client,calls=client_for(login_action())
    tools.submit_browser_response(client,'sess_one','turn_one','req_one',{'type':'browser_authentication','action':'cancel'})
    assert calls[0][1]['events'][0]['response']=={'type':'browser_authentication','action':'cancel'}


def test_mcp_requires_explicit_config_auth_tool_scope(monkeypatch):
    server={'server_label':'known','server_url':'https://mcp.example.com/tools','allowed_tools':['read_issue'],'authorization_env':'TEST_MCP'}
    with pytest.raises(tools.ToolConfigurationError):tools.build_tool_config({'mcp_servers':[server]},verify_connections=False,environ={})
    monkeypatch.setattr(tools,'probe_mcp',lambda *a,**k:True)
    config=tools.build_tool_config({'mcp_servers':[server]},environ={'TEST_MCP':'Bearer synthetic'})
    mcp=next(x for x in config['tools'] if x['type']=='mcp')
    assert mcp['allowed_tools']==['read_issue'] and mcp['transport']['authorization']=='Bearer synthetic'
    assert 'Bearer synthetic' not in json.dumps(config['snapshot'])
    closed=dict(server,enabled=False)
    assert 'mcp' not in {x['type'] for x in tools.build_tool_config({'mcp_servers':[closed]},environ={})['tools']}
    for malformed in [dict(server,allowed_tools=[]),dict(server,allowed_tools=['*']),dict(server,server_url='https://secret:password@example.com'),dict(server,authorization='secret')]:
        with pytest.raises(tools.ToolConfigurationError):tools.validate_settings({'mcp_servers':[malformed]})


def test_mcp_probe_pages_and_rejects_missing_scope():
    class Response:
        headers={'content-type':'application/json'};content=b'body'
        def __init__(self,result):self.result=result
        def raise_for_status(self):pass
        def json(self):return {'result':self.result}
    calls=[]
    def post(url,json,headers):
        calls.append(deepcopy(json))
        if json['method']=='initialize':return Response({'protocolVersion':'2025-03-26'})
        if json['method']=='notifications/initialized':return Response({})
        if json['params'].get('cursor'):return Response({'tools':[{'name':'read_issue'}]})
        return Response({'tools':[{'name':'first'}],'nextCursor':'page2'})
    server={'server_label':'known','server_url':'https://mcp.example.com','allowed_tools':['read_issue']}
    assert tools.probe_mcp(server,http_client=SimpleNamespace(post=post),environ={})
    assert len(calls)==4
    with pytest.raises(tools.ToolConfigurationError):tools.probe_mcp(dict(server,allowed_tools=['missing']),http_client=SimpleNamespace(post=post),environ={})


def test_registered_function_parameters_revocation_and_replay():
    action={'type':'function_call','turn_id':'turn_one','call_id':'call_one','name':'text_statistics','arguments':{'text':'hello world'}}
    client,calls=client_for(action);state=SimpleNamespace(session_id='sess_one',turn_id='turn_one');handled=set()
    tools.handle_function_actions(client,state,{'required_actions':[action]},{'functions':['text_statistics']},handled)
    tools.handle_function_actions(client,state,{'required_actions':[action]},{'functions':['text_statistics']},handled)
    assert len(calls)==1 and json.loads(calls[0][1]['events'][0]['output'])['words']==2
    with pytest.raises(tools.ToolConfigurationError):tools.handle_function_actions(client,state,{'required_actions':[dict(action,call_id='call_two')]},{'functions':[]},set())
    invalid=dict(action,call_id='call_three',arguments={'text':17})
    tools.handle_function_actions(client,state,{'required_actions':[invalid]},{'functions':['text_statistics']},set())
    assert calls[-1][1]['events'][0]['success'] is False


def test_control_marker_never_claims_unobserved_cancel_confirmed():
    folder=tools._control_folder('sess_one','turn_one');tools._write_control(folder/'job.json',{'call_id':'known','status':'running'})
    results=tools.cancel_application_tasks('sess_one','turn_one')
    assert results==[{'call_id':'known','status':'running','confirmed':False}]
    assert (folder/'cancel').exists()
    assert not (tools._control_folder('sess_one','turn_other')/'cancel').exists()


def test_binding_rejects_secret_material(tmp_path):
    store=BindingStore(tmp_path)
    with pytest.raises(ValueError):store.put(owner_identity('alice'),'file',{'settings':{'authorization':'secret'}})
    assert store.get(owner_identity('alice'),'file') is None
