"""Load actual factory/base/wrappers, replacing only provider/config dependencies.

No config files, credentials, provider SDKs or network endpoints are imported.
The main layout preview may supply its real offline-initialized presets module.
"""
import ast
import __future__
from copy import deepcopy
from enum import Enum
import importlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from types import ModuleType, SimpleNamespace
from threading import RLock
from uuid import uuid4
import gradio as gr


class OfflineLocale:
    def __init__(self, root, language='zh_CN'):
        self.language = language
        self.tables = {lang: self.flatten(json.loads((root/'locale'/(lang+'.json')).read_text()))
                       for lang in ('zh_CN','en_US')}
    @staticmethod
    def flatten(tree, prefix=''):
        result = {}
        for key, value in tree.items():
            name = f'{prefix}.{key}' if prefix else key
            if isinstance(value, dict): result.update(OfflineLocale.flatten(value, name))
            else: result[name] = value
        return result
    def __call__(self, key):
        return self.tables.get(self.language, self.tables['en_US']).get(key, self.tables['en_US'].get(key, key))


def definitions(path, names, scope):
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if getattr(node, 'name', None) in names]
    assert len(nodes) == len(names), (path, names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec', flags=__future__.annotations.compiler_flag), scope)


def install(root, history_dir=None, language='zh_CN', presets=None):
    root = Path(root)
    history_dir = Path(history_dir or tempfile.mkdtemp(prefix='chuanhu-agent-history-'))
    history_dir.mkdir(parents=True, exist_ok=True)
    locale = OfflineLocale(root, language)
    if presets is None:
        presets = ModuleType('modules.presets')
        tree = ast.parse((root/'modules/presets.py').read_text())
        scope = {'INITIAL_SYSTEM_PROMPT':'Ordinary system prompt'}
        for name in ('ONLINE_MODELS','DEFAULT_METADATA','MODEL_METADATA'):
            assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                              and any(isinstance(t,ast.Name) and t.id==name for t in node.targets))
            exec(compile(ast.Module(body=[assignment],type_ignores=[]),str(root/'modules/presets.py'),'exec'),scope)
        vars(presets).update(scope)
    presets.i18n = locale
    presets.MODEL_METADATA = {name: dict(deepcopy(presets.DEFAULT_METADATA), **deepcopy(custom))
                              for name, custom in presets.MODEL_METADATA.items()}
    for name, custom in presets.MODEL_METADATA.items():
        custom['model_name'] = custom.get('model_name') or name
    sys.modules['modules.presets'] = presets
    shared = ModuleType('modules.shared')
    shared.chuanhu_path = str(root)
    shared.assets_path = str(root/'web_assets')
    shared.API_HOST = 'api.openai.com'
    shared.state = SimpleNamespace(multi_api_key=False)
    sys.modules['modules.shared'] = shared
    helpers = dict(vars(presets), gr=gr, os=os, json=json, logging=logging, time=time,
                   traceback=traceback, Enum=Enum, shared=shared, RLock=RLock, deepcopy=deepcopy,
                   HISTORY_DIR=str(history_dir), GRADIO_CACHE=str(history_dir/'cache'),
                   get_first_history_name=lambda user='':uuid4().hex+'.json',
                   new_auto_history_filename=lambda user='':uuid4().hex+'.json',
                   get_history_names=lambda user='':[p.stem for p in history_dir.glob('*.json')],
                   init_history_list=lambda *a,**k:gr.update(),
                   get_history_list=lambda *a,**k:gr.update(),
                   construct_user=lambda text:{'role':'user','content':text},
                   construct_assistant=lambda text:{'role':'assistant','content':text},
                   save_file_to_cache=lambda path,cache:path,
                   colorama=SimpleNamespace(Fore=SimpleNamespace(BLUE=''),Style=SimpleNamespace(RESET_ALL='')),
                   TOKEN_OFFSET=1000,REDUCE_TOKEN_FACTOR=.5, STANDARD_ERROR_MSG='Error: ',
                   NO_APIKEY_MSG='No key', NO_INPUT_MSG='No input', beautify_err_msg=lambda text:text)
    definitions(root/'modules/utils.py', {'save_file','save_md_file'}, helpers)
    base = ModuleType('modules.models.base_model')
    vars(base).update(helpers)
    base.__name__ = 'modules.models.base_model'
    definitions(root/'modules/models/base_model.py', {'ModelType','BaseLLMModel'}, vars(base))
    sys.modules[base.__name__] = base
    # Ordinary provider is synthetic; it still uses the actual Base.predict flow.
    class OrdinaryModel(base.BaseLLMModel):
        def __init__(self, model_name, api_key=None, user_name=''):
            super().__init__(model_name, user=user_name, config={'api_key':None})
            self.need_api_key = False
        def prepare_inputs(self, real_inputs, chatbot, **kwargs):
            return False, real_inputs, '', real_inputs, chatbot
        def get_answer_stream_iter(self):
            yield 'Ordinary synthetic response'
        def get_answer_at_once(self):
            return 'Ordinary synthetic response', 1
        def billing_info(self): return 'Offline billing'
    ordinary = ModuleType('modules.models.OpenAIVision')
    ordinary.OpenAIVisionClient = OrdinaryModel
    sys.modules[ordinary.__name__] = ordinary
    sys.modules.pop('modules.models.OpenAIAgents', None)
    transport = importlib.import_module('modules.agent_transport')
    transport.ROOT = root
    agents = importlib.import_module('modules.models.OpenAIAgents')
    agents.connection_for_model = lambda **kwargs: {'api_key': 'offline-fixture-only', 'base_url': 'https://offline.invalid/v1', 'organization': '', 'project': '', 'proxy_env': {}}
    factory = ModuleType('modules.models.models')
    vars(factory).update(helpers, __name__='modules.models.models', __package__='modules.models',
                         config=SimpleNamespace(local_embedding=False), deepcopy=deepcopy,
                         BaseLLMModel=base.BaseLLMModel, ModelType=base.ModelType,
                         hide_middle_chars=lambda key:'masked' if key else '', setPlaceholder=lambda **kwargs:'Offline model preview')
    definitions(root/'modules/models/models.py', {'_get_model','get_model','change_model'}, vars(factory))
    sys.modules[factory.__name__] = factory
    wrappers = dict(helpers)
    names = {'predict','retry','interrupt','billing_info','reset','load_chat_history','set_system_prompt',
             'start_outputing','end_outputing','transfer_input','reset_textbox','auto_name_chat_history'}
    definitions(root/'modules/utils.py', names, wrappers)
    return SimpleNamespace(root=root, presets=presets, base=base, factory=factory, agents=agents,
                           wrappers=wrappers, history_dir=history_dir, locale=locale)
