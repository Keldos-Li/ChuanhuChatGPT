"""Actual main layout/event chains, synthetic providers; no config/credentials."""
import argparse
import ast
import os
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
from threading import Event
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT))
for name in ('ALL_PROXY','all_proxy','HTTP_PROXY','http_proxy','HTTPS_PROXY','https_proxy'):
    os.environ.pop(name,None)
os.environ['GRADIO_ANALYTICS_ENABLED']='False'
os.environ['MPLCONFIGDIR']=tempfile.mkdtemp(prefix='chuanhu-mpl-')
import gradio as gr
from offline_models import OfflineLocale, install
from modules.agent_ui import AgentPanel


def build(language='zh_CN'):
    locale_module=ModuleType('modules.webui_locale')
    locale_module.I18nAuto=lambda:OfflineLocale(ROOT,language)
    sys.modules['modules.webui_locale']=locale_module
    import modules.presets as presets
    temporary=Path(tempfile.mkdtemp(prefix='chuanhu-main-preview-'))
    env=install(ROOT,temporary/'history',language,presets)
    import modules.webui as webui
    # Keep assets real, but all private journals/history inside a synthetic folder.
    cancellation=Event();counter=0
    def worker(command):
        nonlocal counter
        if command['action']=='run':
            counter+=1;cancellation.clear();turn='turn_synthetic_'+str(counter)
            sid=command.get('session_id') or 'sess_synthetic'
            yield dict(type='progress',session_id=sid,turn_id=turn,outcome='in_progress',progress='Synthetic sandbox task running')
            duration=15 if 'slow' in command['prompt'].lower() else 1
            for _ in range(duration*10):
                if cancellation.wait(.1):
                    yield dict(type='result',session_id=sid,turn_id=turn,outcome='cancelled',text='Synthetic task cancelled')
                    return
            yield dict(type='result',session_id=sid,turn_id=turn,outcome='completed',text='Synthetic Agent answer '+str(counter)+': '+command['prompt'])
        elif command['action']=='cancel':
            cancellation.set();yield dict(type='result',outcome='cancel_requested')
        elif command['action']=='download':
            folder=Path(tempfile.mkdtemp(prefix='chuanhu-agent-artifacts-'));file=folder/'synthetic.txt'
            file.write_text('Offline main-chat artifact. No API called.')
            yield dict(type='result',artifacts=[{'id':'artifact_offline','session_id':'sess_synthetic','turn_id':'turn_synthetic_'+str(counter),'path':str(file),'name':file.name,'type':'text/plain','size':file.stat().st_size,'status':'ready'}])
        elif command['action'] in ('inspect','recover'):
            yield dict(type='result',session_id=command['session_id'],turn_id=command['turn_id'],outcome='cancelled' if cancellation.is_set() else 'completed',text='Synthetic read-only recovery')
        else:raise AssertionError(command)
    env.agents.worker_messages=worker
    source=ast.parse((ROOT/'ChuanhuChatbot.py').read_text())
    block=next(node for node in source.body if isinstance(node,ast.With))
    layout=[]
    for node in block.body:
        if isinstance(node,ast.FunctionDef):break
        layout.append(node)
    namespace={name:getattr(presets,name) for name in dir(presets) if not name.startswith('__')}
    namespace.update(env.wrappers)
    namespace.update(AgentPanel=AgentPanel,gr=gr,change_model=env.factory.change_model,CONCURRENT_COUNT=2,
        config=SimpleNamespace(user_avatar=None,bot_avatar=None,http_proxy='',api_host='api.openai.com'),
        my_api_key='',HIDE_MY_KEY=True,multi_api_key=False,check_update=False,show_api_billing=False,
        hide_history_when_not_logged_in=False,advance_docs={'pdf':{}},chat_name_method_index=0,latex_delimiters_set=[],
        get_html=webui.get_html,get_history_names=lambda:[],get_first_history_name=lambda:None,
        get_template_names=lambda:['Offline examples'],load_template=lambda *a,**k:[],hide_middle_chars=lambda value:'',
        repo_tag_html=lambda:'Offline main preview',version_time=lambda:'2026-10-01',versions_html=lambda:'Synthetic providers; no API requests',
        setPlaceholder=lambda **kw:'Offline preview: all responses are synthetic.',
        get_geoip=lambda:'Offline acceptance: actual main chat; synthetic Agent. No API requests.',
        get_history_list=lambda *a:gr.update())
    overwrite=ast.parse((ROOT/'modules/overwrites.py').read_text())
    wrapper=next(node for node in overwrite.body if isinstance(node,ast.FunctionDef) and node.name=='init_with_class_name_as_elem_classes')
    scope={};exec(compile(ast.Module(body=[wrapper],type_ignores=[]),str(ROOT/'modules/overwrites.py'),'exec'),scope)
    gr.components.Component.__init__=scope[wrapper.name](gr.components.Component.__init__)
    gr.blocks.BlockContext.__init__=scope[wrapper.name](gr.blocks.BlockContext.__init__)
    # Exact actual event-chain source, no substitute dropdown or chat callbacks.
    event_prefixes=('cancelBtn.click(', 'user_input.submit(', 'submitBtn.click(',
                    'retryBtn.click(', 'model_select_dropdown.input(', 'systemPromptTxt.change(')
    events=[node for node in block.body
            if (isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id.endswith('_args') for target in node.targets))
            or (isinstance(node,ast.Expr) and ast.unparse(node).startswith(event_prefixes))]
    with gr.Blocks(theme=presets.small_and_beautiful_theme,analytics_enabled=False,title='ChuanhuChat — OFFLINE MAIN CHAT') as demo:
        exec(compile(ast.Module(body=layout,type_ignores=[]),str(ROOT/'ChuanhuChatbot.py'),'exec'),namespace)
        def initial():
            model=env.factory.get_model('GPT3.5 Turbo',user_name='')[0]
            return model,model.system_prompt
        demo.load(initial,outputs=[namespace['current_model'],namespace['systemPromptTxt']])
        exec(compile(ast.Module(body=events,type_ignores=[]),str(ROOT/'ChuanhuChatbot.py'),'exec'),namespace)
    webui.reload_javascript()
    env.agents.shared.chuanhu_path=str(temporary)
    return demo

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8893);parser.add_argument('--language',default='zh_CN');parser.add_argument('--build-only',action='store_true');args=parser.parse_args()
    demo=build(args.language)
    if args.build_only:print('Actual main layout and event chains built:',len(demo.get_config_file()['components']));demo.close()
    else:
        demo.queue().launch(server_name='127.0.0.1',server_port=args.port,share=False,prevent_thread_lock=True,allowed_paths=[str(ROOT/'web_assets')],blocked_paths=[str(ROOT/'config.json'),str(ROOT/'.env.agents'),str(ROOT/'.agents-runtime'),str(ROOT/'agent_data')])
        try:
            while True:time.sleep(1)
        finally:demo.close()
