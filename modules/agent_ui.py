"""Reusable output-file surface and Agent controls for the main chat."""
import html
import json
from copy import deepcopy
import gradio as gr
from optional.agents.tools import FUNCTIONS, tool_availability
from modules.model_capabilities import capabilities

MODEL_EFFORTS = {
    'gpt-6-astra': ['default', 'low', 'medium', 'high', 'xhigh', 'max'],
    'gpt-6-sol': ['default', 'none', 'low', 'medium', 'high', 'xhigh', 'max'],
    'gpt-6.1-sol': ['default', 'low', 'medium', 'high', 'xhigh', 'max'],
}


def _agent(model):
    return model is not None and getattr(model, 'is_hosted_agent', False)


class ArtifactPanel:
    """Output files independent from input attachments and text chat bubbles."""
    def __init__(self):
        with gr.Group(visible=False, elem_id='model-output-files') as self.group:
            gr.Markdown('### 生成的文件')
            self.list = gr.Dataframe(headers=['文件', '类型', '大小', '状态'], datatype=['str'] * 4, interactive=False, wrap=True)
            self.files = gr.File(file_count='multiple', interactive=False, label='下载文件')
            with gr.Row():
                self.retry_id = gr.Dropdown(label='重新获取文件', choices=[])
                self.retry = gr.Button('重试下载')

    @property
    def outputs(self): return [self.group, self.list, self.files, self.retry_id]

    @staticmethod
    def values(model):
        records = getattr(model, '_artifacts', []) if capabilities(model).output_artifacts else []
        rows, paths = [], []
        labels = {'preparing': '准备中', 'ready': '可下载', 'failed': '下载失败'}
        for record in records:
            size = record.get('size')
            rows.append([record['name'], record.get('type', ''), f'{size:,} 字节' if isinstance(size, int) else '待确认',
                         labels.get(record.get('status'), '准备中') + (('：' + record['error']) if record.get('error') else '')])
            if record.get('status') == 'ready' and record.get('path'): paths.append(record['path'])
        return [gr.update(visible=bool(records)), gr.update(value=rows), gr.update(value=paths),
                gr.update(choices=[(record['name'] + ' · ' + record['id'], record['id']) for record in records], value=None)]


def describe_settings(model):
    if not _agent(model): return ''
    configured = model._tool_settings
    current = (model._session_settings or {}).get('tools')
    lines = [model._notice] if model._notice else []
    if model._pending_network is not None:
        lines.append('待确认的新会话联网设置：' + ('开启' if model._pending_network else '关闭') + '；点击按新配置新建并继续后生效')
    def summary(settings):
        enabled = [label for label, state, _ in tool_availability(settings) if state == '已启用']
        return ('云端执行环境联网：' + ('开' if settings['network'] else '关') + '；内置网页搜索：'
                + (settings['search_mode'] if settings['web_search'] else '关') + '；能力：' + '、'.join(enabled))
    lines.append('新会话将使用：' + summary(configured))
    if current:
        lines.append('当前会话实际设置：' + summary(current))
        if current != configured or model.system_prompt != model._session_settings.get('instructions'):
            lines.append('配置有变更。继续原会话会保持原设置；按新配置新建并继续只带入文字，原聊天会保留。')
    lines.append('已生效模型：' + model.model_name + '；推理：' + (model._reasoning or '模型默认'))
    return '\n\n'.join(lines)


def browser_form(model, request_id=None, request: gr.Request = None):
    if _agent(model) and request is not None: model.bind_owner(request)
    if not _agent(model): return '', gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)
    cards = model._pending_actions
    card = next((card for card in cards if card['request_id'] == request_id), cards[0] if cards else None)
    if not card: return '', gr.update(visible=False), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)
    request = card['request']
    origin = request.get('origin') or request.get('credential_origin') or '未提供目标网站，不能提交登录'
    content = '<div id="agent-browser-form" data-request-id="' + html.escape(card['request_id'], quote=True) + '">'
    content += '<p><strong>等待授权或登录</strong></p><p>目标网站：' + html.escape(origin) + '</p>'
    content += '<p>' + html.escape(request.get('reason') or 'Agent 需要处理这个网站请求才能继续') + '</p>'
    login = request.get('type') == 'browser_authentication'
    if not login: content += '<p>批准范围仅为上述网站 origin；不会批准其他网站，也不代表同意任意外部操作。</p>'
    else:
        content += '<p>请核对网站。表单值只提交到专用登录接口，不进入聊天记录；提交后会清空。所有输入均隐藏显示。</p>'
        options = request.get('options') or []
        if options:
            content += '<label>登录方式 <select id="agent-login-option" onchange="window.chuanhuAgentLoginOption?.()">'
            content += ''.join('<option value="' + html.escape(option['id'], quote=True) + '" data-fields="' + html.escape(json.dumps(option.get('field_ids', [])), quote=True) + '">' + html.escape(option['label']) + '</option>' for option in options)
            content += '</select></label>'
        first_fields = set(options[0].get('field_ids', [])) if options else None
        for field in request.get('fields') or []:
            hidden = first_fields is not None and field['id'] not in first_fields
            content += '<label data-agent-field="' + html.escape(field['id'], quote=True) + '"' + (' hidden' if hidden else '') + '>'
            content += html.escape(field.get('label') or field['id']) + ('（必填）' if field.get('required') else '')
            content += '<input type="password" autocomplete="off" data-field-id="' + html.escape(field['id'], quote=True) + '" /></label>'
    content += '</div>'
    return content, gr.update(visible=not login), gr.update(visible=not login), gr.update(visible=True), gr.update(visible=login and bool(request.get('credential_origin')))


class AgentPanel:
    def selectors(self):
        with gr.Row(visible=False, elem_id='agent-send-options') as self.selection_group:
            self.model = gr.Dropdown(label='Agent 子模型', choices=list(MODEL_EFFORTS), value='gpt-6-astra', allow_custom_value=True, min_width=150)
            self.reasoning = gr.Dropdown(label='推理强度', choices=MODEL_EFFORTS['gpt-6-astra'], value='default', min_width=120)
            self.apply_model = gr.Button('应用到下一轮', size='sm', min_width=110)

    def output_components(self):
        self.artifacts = ArtifactPanel()
        with gr.Group(visible=False, elem_id='agent-browser-requests') as self.browser_group:
            self.request_id = gr.Dropdown(label='当前网站请求', choices=[])
            self.browser_html = gr.HTML()
            with gr.Row():
                self.approve = gr.Button('批准此网站', visible=False)
                self.deny = gr.Button('拒绝此网站', visible=False)
                self.cancel_request = gr.Button('取消此请求', visible=False)
                self.login_submit = gr.Button('提交登录', visible=False)
            self.payload = gr.Textbox(type='password', visible=False)

    def settings_components(self):
        with gr.Accordion('Agent 工具与能力', open=False, visible=False, elem_id='agent-tool-settings') as self.settings_group:
            gr.Markdown('模型是否可用及推理组合由当前 API 地址和账号权限校验。设置保存后用于新会话。云端执行环境联网和网页搜索分别控制。MCP 只开放明确配置的服务器和工具，凭据使用服务器环境变量引用。')
            self.settings_status = gr.Markdown()
            self.network = gr.Checkbox(label='云端执行环境联网', value=True)
            self.code = gr.Checkbox(label='代码与文件执行', value=True)
            self.search = gr.Checkbox(label='内置网页搜索', value=True)
            self.search_mode = gr.Dropdown(label='搜索模式', choices=['live', 'cached', 'disabled'], value='live')
            self.domains = gr.Textbox(label='搜索域名范围（每行一个，可留空）')
            self.browser = gr.Checkbox(label='云端浏览器', value=True)
            self.screenshots = gr.Checkbox(label='返回浏览器截图', value=False)
            self.discovery = gr.Checkbox(label='工具发现（配置可执行函数后生效）', value=True)
            self.programmatic = gr.Checkbox(label='程序化工具调用', value=True)
            self.functions = gr.CheckboxGroup(label='已注册的应用函数', choices=list(FUNCTIONS), value=[])
            self.mcp = gr.Textbox(label='MCP 服务器配置（JSON，勿填真实凭据）', value='[]', lines=5)
            self.availability = gr.Dataframe(headers=['能力', '状态', '说明'], datatype=['str'] * 3, interactive=False, wrap=True)
            self.save = gr.Button('保存新会话配置')
            self.fork = gr.Button('按新配置新建并继续')
            self.reconnect = gr.Button('重新连接原会话')

    @property
    def config_inputs(self):
        return [self.network, self.code, self.search, self.search_mode, self.domains, self.browser, self.screenshots, self.discovery, self.programmatic, self.functions, self.mcp]

    @property
    def outputs(self):
        return [self.selection_group, self.settings_group, self.settings_status, self.model, self.reasoning, self.apply_model,
                self.browser_group, self.request_id, self.browser_html, self.approve, self.deny, self.cancel_request, self.login_submit,
                *self.artifacts.outputs, *self.config_inputs, self.availability]

    @staticmethod
    def values(model, request: gr.Request = None, include_config=True):
        enabled = _agent(model)
        if enabled and request is not None: model.bind_owner(request)
        if not enabled:
            return [gr.update(visible=False), gr.update(visible=False), '', gr.update(), gr.update(), gr.update(),
                    gr.update(visible=False), gr.update(choices=[], value=None), '', *[gr.update(visible=False)] * 4,
                    *ArtifactPanel.values(model), *[gr.update()] * 12]
        busy = model._running or model._state.get('outcome') not in ('not_started', 'completed', 'cancelled', 'failed') or model._needs_sync
        cards = model._pending_actions
        chosen = cards[0]['request_id'] if cards else None
        browser = browser_form(model, chosen)
        settings = model._tool_settings
        config = [settings['network'], settings['code_execution'], settings['web_search'], settings['search_mode'], '\n'.join(settings['search_domains']),
                  settings['computer_use'], settings['include_screenshots'], settings['tool_search'], settings['programmatic_tool_calling'], settings['functions'],
                  json.dumps(settings['mcp_servers'], ensure_ascii=False, indent=2)]
        return [gr.update(visible=True), gr.update(visible=True), describe_settings(model),
                gr.update(value=model.model_name, interactive=not busy), gr.update(value=model._reasoning or 'default', choices=MODEL_EFFORTS.get(model.model_name, ['default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']), interactive=not busy), gr.update(interactive=not busy),
                gr.update(visible=bool(cards)), gr.update(choices=[((card['request'].get('origin') or card['request'].get('credential_origin') or '网站请求') + ' · ' + card['request_id'], card['request_id']) for card in cards], value=chosen),
                *browser, *ArtifactPanel.values(model), *(config if include_config else [gr.update()] * len(config)), gr.update(value=tool_availability(settings))]

    def wrap_predict(self, predict, capability_ui):
        def predict_with_ui(model, inputs, chatbot, use_websearch=False, files=None, reply_language=None, request: gr.Request = None):
            for chat, status in predict(model, inputs, chatbot, use_websearch, files, reply_language, request=request):
                yield chat, status, *self.values(model, request=request, include_config=False), *capability_ui.values(model)
            yield gr.update(), gr.update(), *self.values(model, request=request, include_config=False), *capability_ui.values(model)
        return predict_with_ui

    def wire(self, current_model, chatbot, status_display, capability_ui=None):
        def apply(model, name, effort, request: gr.Request):
            model.bind_owner(request)
            try: message = model.set_agent_model(name, effort)
            except Exception as error: message = str(error)
            return message, *self.values(model)
        self.model.input(lambda name: gr.update(choices=MODEL_EFFORTS.get(name, ['default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']), value='default'), [self.model], [self.reasoning])
        self.apply_model.click(apply, [current_model, self.model, self.reasoning], [status_display, *self.outputs], queue=False)
        def save(model, network, code, search, mode, domains, browser, screenshots, discovery, programmatic, functions, mcp, request: gr.Request):
            model.bind_owner(request)
            try:
                value = {'network': network, 'code_execution': code, 'web_search': search, 'search_mode': mode, 'search_domains': [s.strip() for s in domains.splitlines() if s.strip()],
                         'computer_use': browser, 'include_screenshots': screenshots, 'tool_search': discovery, 'programmatic_tool_calling': programmatic, 'functions': functions, 'mcp_servers': json.loads(mcp)}
                message = model.save_agent_tools(value)
            except Exception as error: message = str(error) if not isinstance(error, json.JSONDecodeError) else 'MCP 配置不是有效 JSON'
            return message, *self.values(model)
        self.save.click(save, [current_model, *self.config_inputs], [status_display, *self.outputs], queue=False)
        def fork(model, request: gr.Request):
            model.bind_owner(request)
            return model.new_session_from_history()
        self.fork.click(fork, [current_model], [chatbot, status_display]).then(self.values, [current_model], self.outputs)
        def reconnect(model, request: gr.Request):
            if _agent(model):
                model.bind_owner(request)
                for chat, status in model.reconnect():
                    if capability_ui is None: yield chat, status
                    else: yield chat, status, *capability_ui.values(model), *self.values(model, request=request, include_config=False)
                # Generator cleanup clears the observer's running state only
                # after its last yield; update the one main Stop/Send pair now.
                if capability_ui is not None:
                    yield gr.update(), model._status(), *capability_ui.values(model), *self.values(model, request=request, include_config=False)
        reconnect_outputs = [chatbot, status_display] + (capability_ui.outputs + self.outputs if capability_ui is not None else [])
        self.reconnect.click(reconnect, [current_model], reconnect_outputs)
        def retry_file(model, identifier, request: gr.Request):
            model.bind_owner(request)
            for _, status in model.retry_artifact(identifier):
                yield status, *self.values(model, request=request, include_config=False)
        self.artifacts.retry.click(retry_file, [current_model, self.artifacts.retry_id], [status_display, *self.outputs])
        self.request_id.input(browser_form, [current_model, self.request_id], [self.browser_html, self.approve, self.deny, self.cancel_request, self.login_submit])
        def origin(model, identifier, decision):
            card = next((card for card in model._pending_actions if card['request_id'] == identifier), None)
            if card is None: raise gr.Error('该网站请求已失效')
            request_type = card['request']['type']
            response = {'type': request_type, 'action': 'cancel'} if request_type == 'browser_authentication' else {'type': request_type, 'decision': decision}
            return model.respond_browser(identifier, response), *self.values(model)
        def origin_callback(decision):
            def respond(model, identifier, request: gr.Request):
                model.bind_owner(request)
                return origin(model, identifier, decision)
            return respond
        for button, decision in ((self.approve, 'approve'), (self.deny, 'deny'), (self.cancel_request, 'cancel')):
            button.click(origin_callback(decision), [current_model, self.request_id], [status_display, *self.outputs], queue=False)
        def login(model, identifier, payload, request: gr.Request):
            model.bind_owner(request)
            try:
                response = json.loads(payload)
                message = model.respond_browser(identifier, response)
            except Exception as error:
                message = str(error) if isinstance(error, gr.Error) else '登录提交失败；请重新连接确认当前请求，勿重复提交'
            finally: payload = None
            return '', message, *self.values(model)
        self.login_submit.click(login, [current_model, self.request_id, self.payload], [self.payload, status_display, *self.outputs], queue=False,
            js='''(model, id, unused) => {
                const root = (typeof gradioApp === 'function' ? gradioApp() : document).querySelector('#agent-browser-form');
                if (!root || root.dataset.requestId !== id) return [model, id, '{}'];
                const option = root.querySelector('#agent-login-option');
                const allowed = option ? JSON.parse(option.selectedOptions[0].dataset.fields) : null;
                const fields = Array.from(root.querySelectorAll('input[data-field-id]')).filter(x => allowed === null || allowed.includes(x.dataset.fieldId)).map(x => ({field_id: x.dataset.fieldId, value: x.value}));
                const response = {type:'browser_authentication', action:'submit', fields};
                if (option) response.selected_option = option.value;
                root.querySelectorAll('input').forEach(x => x.value='');
                return [model, id, JSON.stringify(response)];
            }''')
        # A model output updates progress, file readiness and pending forms without
        # another message or a queued request behind the running generator.
        def live_values(model, request: gr.Request):
            return self.values(model, request=request, include_config=False)
        chatbot.change(live_values, [current_model], self.outputs, queue=False, show_progress=False)
