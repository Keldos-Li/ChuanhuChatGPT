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
from gradio.components.chatbot import ChatbotData, FileMessage
from gradio.data_classes import FileData
from gradio_client import utils as client_utils
from offline_models import OfflineLocale, install
from modules.agent.ui import AgentPanel
from modules.model_capabilities import CapabilityUI


def build(language='zh_CN', hide_my_key=True):
    locale_module=ModuleType('modules.webui_locale')
    locale_module.I18nAuto=lambda:OfflineLocale(ROOT,language)
    sys.modules['modules.webui_locale']=locale_module
    import modules.presets as presets
    temporary=Path(tempfile.mkdtemp(prefix='chuanhu-main-preview-'))
    fixtures=temporary/'synthetic-inputs';fixtures.mkdir()
    for name,content in [('notes.txt','Synthetic notes for Agent attachment QA'),('metrics.csv','day,value\n1,10\n2,20'),
                         ('slow-upload.txt','Synthetic cancellable preparation'),('upload-fail.txt','Synthetic upload failure')]:
        (fixtures/name).write_text(content)
    for index in ('a','b'):
        folder=fixtures/index;folder.mkdir();(folder/'same.txt').write_text('Different synthetic content '+index)
    print('Synthetic input fixtures:',fixtures)
    env=install(ROOT,temporary/'history',language,presets)
    presets.HISTORY_DIR = str(temporary/'history')
    from modules.history_selection import load_history_model
    import modules.webui as webui
    webui.get_html = lambda filename: (ROOT/"web_assets"/"html"/filename).read_text()
    utility_source = ast.parse((ROOT/'modules/utils.py').read_text())
    placeholder_node = next(node for node in utility_source.body if isinstance(node, ast.FunctionDef) and node.name == 'setPlaceholder')
    placeholder_scope = dict(__name__='modules.utils', __package__='modules', MODEL_METADATA=presets.MODEL_METADATA, i18n=presets.i18n, BaseLLMModel=object)
    exec(compile(ast.Module(body=[placeholder_node], type_ignores=[]), '<real-placeholder>', 'exec'), placeholder_scope)
    env.factory.setPlaceholder = placeholder_scope['setPlaceholder']
    # Keep assets real, but all private journals/history inside a synthetic folder.
    from main_chat_mock import MainChatMock
    synthetic_service=MainChatMock()
    env.agents.worker_messages=synthetic_service.worker
    source=ast.parse((ROOT/'ChuanhuChatbot.py').read_text())
    block=next(node for node in source.body if isinstance(node,ast.With))
    layout=[]
    for node in block.body:
        if isinstance(node,ast.FunctionDef):break
        layout.append(node)
    namespace={name:getattr(presets,name) for name in dir(presets) if not name.startswith('__')}
    namespace.update(env.wrappers)
    namespace.update(load_history_model=load_history_model,AgentPanel=AgentPanel,CapabilityUI=CapabilityUI,gr=gr,change_model=env.factory.change_model,CONCURRENT_COUNT=2,
        config=SimpleNamespace(user_avatar=None,bot_avatar=None,http_proxy='',api_host='api.openai.com'),
        my_api_key='',HIDE_MY_KEY=hide_my_key,multi_api_key=False,check_update=False,show_api_billing=False,
        hide_history_when_not_logged_in=False,advance_docs={'pdf':{}},chat_name_method_index=0,latex_delimiters_set=[],
        get_html=webui.get_html,get_history_names=lambda:[],get_first_history_name=lambda:None,
        get_template_names=lambda:['Offline examples'],load_template=lambda *a,**k:[],hide_middle_chars=lambda value:'',
        repo_tag_html=lambda:'Offline main preview',version_time=lambda:'2026-10-01',versions_html=lambda:'Synthetic providers; no API requests',
        setPlaceholder=placeholder_scope['setPlaceholder'],
        get_geoip=lambda:'Offline acceptance: actual main chat; synthetic Agent. No API requests.',
        get_history_list=lambda *a:gr.update())
    overwrite=ast.parse((ROOT/'modules/overwrites.py').read_text())
    patches=[node for node in overwrite.body if isinstance(node,ast.FunctionDef) and node.name in
             ('init_with_class_name_as_elem_classes','postprocess','postprocess_chat_messages')]
    scope=dict(ChatbotData=ChatbotData,FileMessage=FileMessage,FileData=FileData,client_utils=client_utils,
               convert_user_before_marked=env.wrappers['convert_user_before_marked'],
               convert_bot_before_marked=env.wrappers['convert_bot_before_marked'])
    exec(compile(ast.Module(body=patches,type_ignores=[]),str(ROOT/'modules/overwrites.py'),'exec'),scope)
    gr.components.Component.__init__=scope['init_with_class_name_as_elem_classes'](gr.components.Component.__init__)
    gr.blocks.BlockContext.__init__=scope['init_with_class_name_as_elem_classes'](gr.blocks.BlockContext.__init__)
    # Ordinary replies need the same raw/Markdown pair as production. Agent
    # projection already contains it, and the real postprocessor is idempotent.
    gr.Chatbot._postprocess_chat_messages=scope['postprocess_chat_messages']
    gr.Chatbot.postprocess=scope['postprocess']
    # Exact actual event-chain source, no substitute dropdown or chat callbacks.
    event_prefixes=('cancelBtn.click(', 'user_input.submit(', 'submitBtn.click(',
                    'retryBtn.click(', 'model_select_dropdown.input(', 'systemPromptTxt.change(',
                    'emptyBtn.click(', 'historySelectList.select(')
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
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8893);parser.add_argument('--language',default='zh_CN');parser.add_argument('--build-only',action='store_true');parser.add_argument('--show-api-key',action='store_true');args=parser.parse_args()
    demo=build(args.language, hide_my_key=not args.show_api_key)
    if args.build_only:print('Actual main layout and event chains built:',len(demo.get_config_file()['components']));demo.close()
    else:
        demo.queue().launch(server_name='127.0.0.1',server_port=args.port,share=False,prevent_thread_lock=True,allowed_paths=[str(ROOT/'web_assets')],blocked_paths=[str(ROOT/'config.json'),str(ROOT/'.env.agents'),str(ROOT/'.agents-runtime'),str(ROOT/'agent_data')])
        try:
            while True:time.sleep(1)
        finally:demo.close()
