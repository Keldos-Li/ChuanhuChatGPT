"""Reusable output-file surface and Agent controls for the main chat."""
import html
import json
import inspect
from functools import wraps
from copy import deepcopy
import gradio as gr
from optional.agents.tools import FUNCTIONS, tool_availability
from modules.model_capabilities import capabilities

MODEL_EFFORTS = {
    'gpt-6-astra': ['default', 'low', 'medium', 'high', 'xhigh', 'max'],
    'gpt-6-sol': ['default', 'none', 'low', 'medium', 'high', 'xhigh', 'max'],
    'gpt-6.1-sol': ['default', 'low', 'medium', 'high', 'xhigh', 'max'],
}
REASONING_CHOICES = ['default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']


def _agent(model):
    return model is not None and getattr(model, 'is_hosted_agent', False)


def message_file_projection(model, rows=None):
    from modules.agent_message_files import project_message_files
    return project_message_files(deepcopy(model._display if rows is None else rows), model._cloud_items, model._artifacts,
        session_id=model._state.get('session_id'), conversation_id=model._conversation_id,
        current_turn_id=model._state.get('turn_id'), answer_row=model._answer_row)


def _chat_frames(updates):
    """Rebase shrinking histories instead of emitting index-shifting diffs.

    Gradio 4.29 deletes list entries in ascending index order while its client
    applies them with splice. A local preview can have more rows than the
    authoritative session. Changing the update envelope forces a root replace.
    """
    previous_count = None
    wrapped = False
    for chat, status in updates:
        if isinstance(chat, (list, tuple)):
            count = len(chat)
            wrapped = previous_count is not None and count < previous_count and not wrapped
            previous_count = count
            if wrapped: chat = gr.update(value=deepcopy(chat))
        yield chat, status


class ArtifactPanel:
    """Output files independent from input attachments and text chat bubbles."""
    def __init__(self):
        with gr.Column(visible=False, elem_id='model-output-files', min_width=0, scale=0) as self.group:
            self.list = gr.HTML(elem_id='model-output-cards')
            # CSS hides these native controls without unmounting their actual
            # download and callback behavior. Cards address files by artifact ID.
            self.files = gr.File(file_count='multiple', interactive=False, label='[]', elem_id='model-output-native-files')
            self.retry_id = gr.Textbox(elem_id='model-output-retry-id', show_label=False)
            self.retry = gr.Button('重试下载', elem_id='model-output-retry')

    @property
    def outputs(self): return [self.group, self.list, self.files, self.retry_id]

    @staticmethod
    def values(model):
        records = getattr(model, '_artifacts', []) if capabilities(model).output_artifacts else []
        anchors = message_file_projection(model).artifact_anchors if _agent(model) else {}
        cards, paths, ready_ids = [], [], []
        labels = {'preparing': '准备中', 'ready': '', 'failed': '下载失败'}
        for record in records:
            size = record.get('size')
            size_text = f'{size:,} 字节' if isinstance(size, int) else '大小待确认'
            status = record.get('status', 'preparing')
            action = 'retry' if status == 'failed' else 'download' if status == 'ready' and record.get('path') else ''
            if action == 'download':
                paths.append(record['path']); ready_ids.append(record['id'])
            status_text = labels.get(status, '准备中')
            if status == 'failed': status_text += '，点击重试'
            error = ('：' + str(record['error'])) if record.get('error') else ''
            escape = lambda value: html.escape(str(value), quote=True)
            # Feather's generic file icon, also used by the installed Gradio UI.
            icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>'
            cards.append('<button type="button" class="model-file-card" data-artifact-id="' + escape(record['id']) + '" data-message-key="' + escape(anchors.get(record['id'], '')) + '" data-conversation-id="' + escape(getattr(model, '_conversation_id', '')) + '" data-file-action="' + action + '" aria-label="' + escape(record['name'] + '，' + (status_text or '下载文件')) + '"' + ('' if action else ' disabled="disabled"') + '>'
                         + '<span class="model-file-icon">' + icon + '</span><span class="model-file-content">'
                         + '<span class="model-file-name" title="' + escape(record['name']) + '">' + escape(record['name']) + '</span>'
                         + '<span class="model-file-meta"><span class="model-file-size">' + size_text + '</span>' + (' · <span class="model-file-state">' + escape(status_text) + '</span>' if status_text else '') + '</span>'
                         + '<span class="model-file-error">' + escape(error) + '</span><span class="model-file-feedback" aria-live="polite"></span></span></button>')
        markup = '<div class="model-file-cards" aria-label="生成的文件">' + ''.join(cards) + '</div>' if cards else ''
        # The hidden native label travels with its File value, so the browser
        # maps stable IDs to links from the same render, even with duplicate names.
        return [gr.update(visible=bool(records)), gr.update(value=markup), gr.update(value=paths, label=json.dumps(ready_ids)), gr.update(value='')]


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
    def __init__(self, format_user=None, format_assistant=None):
        self.format_user = format_user or (lambda value: value)
        self.format_assistant = format_assistant or (lambda value: value)

    def activity_value(self, model, status=None):
        """Ephemeral conversation feedback, never assistant text or history."""
        if not _agent(model): return gr.update(value='', visible=False)
        # A late selector/config callback deliberately returns a no-op update.
        # Do not turn it into a fresh status that can overwrite newer feedback.
        if isinstance(status, dict) and status.get('__type__') == 'update' and 'value' not in status:
            return gr.update()
        default = model._status()
        text = status if isinstance(status, str) else default
        # Submission feedback is useful while authorization resumes, but is
        # stale once that turn has completed. Keep actual recovery notices.
        finished_authorization = model._state.get('outcome') == 'completed' and model._notice == '已提交网站请求，等待继续；登录结果尚未确认'
        if model._state.get('outcome') in ('not_started', 'completed') and text == default and (not model._notice or finished_authorization):
            text = ''
        return gr.update(value=text, visible=bool(text))

    def status_callback(self, callback, index=0, *, header=False, returned_model=None, input_feedback=False):
        """Keep ordinary header results; route only Agent feedback locally."""
        def project(result, args):
            values = list(result) if isinstance(result, (tuple, list)) else [result]
            model = values[returned_model] if returned_model is not None else next(
                (arg for arg in args if hasattr(arg, 'chatbot')), None)
            if input_feedback:
                text = values[index]
                local = gr.update(value=text, visible=bool(text))
            else: local = self.activity_value(model, values[index])
            if header:
                if _agent(model): values[index] = ''
                return (*values, local)
            values[index] = local
            return tuple(values) if isinstance(result, (tuple, list)) else values[0]
        if inspect.isgeneratorfunction(callback):
            @wraps(callback)
            def generator(*args, **kwargs):
                for result in callback(*args, **kwargs): yield project(result, (*args, *kwargs.values()))
            return generator
        @wraps(callback)
        def call(*args, **kwargs): return project(callback(*args, **kwargs), (*args, *kwargs.values()))
        return call

    def render_chat(self, model, rows):
        if not _agent(model): return rows
        from modules.agent_message_files import render_projection
        if isinstance(rows, dict) and rows.get('__type__') == 'update':
            if 'value' not in rows: return rows
            return dict(rows, value=self.render_chat(model, rows['value']))
        return render_projection(message_file_projection(model, rows), self.format_user, self.format_assistant)

    def chat_value(self, model, request: gr.Request):
        if not _agent(model): return gr.update()
        model.bind_owner(request)
        return gr.update(value=self.render_chat(model, model.chatbot))

    def input_components(self):
        with gr.Column(visible=False, elem_id='agent-input-files', min_width=0, scale=0) as self.input_group:
            self.input_feedback = gr.Markdown('', visible=False, elem_id='agent-input-feedback')
            self.input_picker = gr.File(file_count='multiple', type='filepath', label='添加消息附件', show_label=False,
                                        elem_id='agent-upload-files')
            self.input_files = gr.File(file_count='multiple', type='filepath', label='{}', show_label=True,
                                       elem_id='agent-pending-files')
            self.input_target = gr.Textbox(visible=False)
            self.input_remove_payload = gr.Textbox(elem_id='agent-input-remove-payload', show_label=False)
            self.input_remove = gr.Button('移除附件', elem_id='agent-input-remove')

    @staticmethod
    def input_value(model, interactive=None):
        if not _agent(model): return gr.update(value=[], label='{}', interactive=False)
        with model._lock:
            if interactive is None:
                interactive = not (model._running or bool(getattr(model, '_pending_send', None)) or model._needs_sync
                                   or model._state.get('outcome') not in ('not_started', 'completed', 'cancelled', 'failed'))
            records = model._input_stager.snapshot() if model._input_stager is not None else ()
            metadata = {'target': model._conversation_id, 'ids': [record.input_id for record in records]}
            return gr.update(value=list(model._pending_upload_paths), label=json.dumps(metadata), interactive=interactive)

    def selectors(self):
        with gr.Column(visible=False, elem_id='agent-model-options', min_width=0) as self.selection_group:
            self.model = gr.Dropdown(label='使用模型', choices=list(MODEL_EFFORTS), value='gpt-6-astra', allow_custom_value=True, min_width=150)
            self.reasoning = gr.Dropdown(label='推理强度', choices=REASONING_CHOICES, value='default', info='选择后自动用于下一轮对话', min_width=120)
            self.choice_revision = gr.Number(value=0, precision=0, visible=False)

    def output_components(self):
        self.artifacts = ArtifactPanel()
        self.activity = gr.Markdown('', visible=False, elem_id='agent-activity-feedback')
        with gr.Column(visible=False, elem_id='agent-browser-requests', min_width=0, scale=0) as self.browser_group:
            self.request_id = gr.Dropdown(label='当前网站请求', choices=[])
            self.browser_html = gr.HTML()
            with gr.Row():
                self.approve = gr.Button('批准此网站', visible=False)
                self.deny = gr.Button('拒绝此网站', visible=False)
                self.cancel_request = gr.Button('取消此请求', visible=False)
                self.login_submit = gr.Button('提交登录', visible=False)
            self.payload = gr.Textbox(type='password', visible=False)

    def settings_components(self):
        with gr.Column(visible=False, elem_id='agent-tool-settings', min_width=0) as self.settings_group:
            self.settings_status = gr.Markdown(visible=False)
            self.network = gr.Checkbox(label='云端执行环境联网', value=True, elem_id='agent-network-access', elem_classes='switch-checkbox')
            self.code = gr.Checkbox(label='代码与文件执行', value=True, elem_classes='switch-checkbox')
            self.search = gr.Checkbox(label='内置网页搜索', value=True, elem_classes='switch-checkbox')
            self.search_mode = gr.Dropdown(label='搜索模式', choices=['live', 'cached', 'disabled'], value='live')
            self.domains = gr.Textbox(label='搜索域名范围（每行一个，可留空）')
            self.browser = gr.Checkbox(label='云端浏览器', value=True, elem_classes='switch-checkbox')
            self.screenshots = gr.Checkbox(label='返回浏览器截图', value=False, elem_classes='switch-checkbox')
            self.discovery = gr.Checkbox(label='工具发现（配置可执行函数后生效）', value=True, elem_classes='switch-checkbox')
            self.programmatic = gr.Checkbox(label='程序化工具调用', value=True, elem_classes='switch-checkbox')
            self.functions = gr.CheckboxGroup(label='已注册的应用函数', choices=list(FUNCTIONS), value=[])
            self.mcp = gr.Textbox(label='MCP 服务器', value='[]', lines=5)
            self.availability = gr.Dataframe(headers=['能力', '状态', '说明'], datatype=['str'] * 3, interactive=False, wrap=True, visible=False)
            self.fork = gr.Button('按新配置新建并继续', visible=False)
            self.reconnect = gr.Button('重新连接原会话')
            self.tool_revision = gr.Number(value=0, precision=0, visible=False)
            self.config_target = gr.Textbox(visible=False)

    @staticmethod
    def config_from_inputs(network, code, search, mode, domains, browser, screenshots, discovery, programmatic, functions, mcp):
        return {'network': network, 'code_execution': code, 'web_search': search, 'search_mode': mode,
                'search_domains': [s.strip() for s in domains.splitlines() if s.strip()],
                'computer_use': browser, 'include_screenshots': screenshots, 'tool_search': discovery,
                'programmatic_tool_calling': programmatic, 'functions': functions, 'mcp_servers': json.loads(mcp)}

    def wrap_transfer(self, transfer):
        def transfer_input(inputs, current_model=None, agent_model=None, agent_reasoning='default', agent_choice_revision=None, agent_files=None,
                           network=None, code=None, search=None, mode=None, domains=None, browser=None, screenshots=None,
                           discovery=None, programmatic=None, functions=None, mcp=None, instructions=None, target=None, tool_revision=None,
                           request: gr.Request = None):
            if _agent(current_model):
                current_model.bind_owner(request)
                with current_model._lock:
                    if network is not None:
                        try: config = self.config_from_inputs(network, code, search, mode, domains, browser, screenshots, discovery, programmatic, functions, mcp)
                        except (TypeError, ValueError, AttributeError): raise gr.Error('工具配置格式无效，请检查后发送') from None
                        current_model.freeze_agent_configuration(config, instructions, tool_revision, target)
                    result = list(transfer(inputs, current_model, agent_model, agent_reasoning, agent_choice_revision, agent_files, request=request))
                    # Keep the draft until the browser sees submission accepted.
                    # Failed preparation never needs a delayed server writeback.
                    result[1] = gr.update()
                    return tuple(result)
            return transfer(inputs, current_model, agent_model, agent_reasoning, agent_choice_revision, agent_files, request=request)
        return transfer_input

    def bind_sidebar(self, tab, prompt, template, prompt_group, ordinary_label):
        self.sidebar = (tab, prompt, template, prompt_group, ordinary_label)

    def sidebar_values(self, model):
        if not hasattr(self, 'sidebar'): return []
        enabled = _agent(model)
        locked = enabled and (bool(model._state.get('session_id')) or model._running or model._needs_sync
                              or model._state.get('outcome') not in ('not_started', 'completed', 'cancelled', 'failed')
                              or bool(getattr(model, '_pending_send', None)))
        return [gr.update(label='Agent' if enabled else self.sidebar[-1]),
                gr.update(**({'value':model.system_prompt} if locked else {}), interactive=not locked, label='系统提示词' if enabled else 'System prompt', lines=4 if enabled else 8),
                gr.update(interactive=not locked), gr.update(label='系统提示词' if enabled else 'Prompt')]

    def finish_submission(self, model, envelope, draft, request: gr.Request):
        if not _agent(model): return gr.update(), gr.update()
        model.bind_owner(request)
        with model._lock:
            if not isinstance(envelope, dict) or envelope.get('target') != id(model) or envelope.get('token') != getattr(model, '_draft_token', None) or getattr(model, '_draft_conversation', None) != model._conversation_id:
                return gr.update(), gr.update()
            return gr.update(), model._status()

    @property
    def config_inputs(self):
        return [self.network, self.code, self.search, self.search_mode, self.domains, self.browser, self.screenshots, self.discovery, self.programmatic, self.functions, self.mcp]

    @property
    def outputs(self):
        return [self.selection_group, self.settings_group, self.settings_status, self.model, self.reasoning,
                self.browser_group, self.request_id, self.browser_html, self.approve, self.deny, self.cancel_request, self.login_submit,
                *self.artifacts.outputs, *self.config_inputs, self.availability] + ([self.input_group, self.input_files, self.input_picker, self.input_target] if hasattr(self, 'input_files') else []) + (list(self.sidebar[:4]) if hasattr(self, 'sidebar') else []) + [self.config_target]

    def values(self, model, request: gr.Request = None, include_config=True):
        enabled = _agent(model)
        if enabled and request is not None: model.bind_owner(request)
        if not enabled:
            return [gr.update(visible=False), gr.update(visible=False), '', gr.update(), gr.update(),
                    gr.update(visible=False), gr.update(choices=[], value=None), '', *[gr.update(visible=False)] * 4,
                    *ArtifactPanel.values(model), *[gr.update()] * 12] + ([gr.update(visible=False), gr.update(value=[], interactive=False), gr.update(value=[], interactive=False), ''] if hasattr(self, 'input_files') else []) + self.sidebar_values(model) + ['']
        busy = model._running or bool(getattr(model, '_pending_send', None)) or model._state.get('outcome') not in ('not_started', 'completed', 'cancelled', 'failed') or model._needs_sync
        next_model, next_reasoning = model.agent_model_choice
        cards = model._pending_actions
        chosen = cards[0]['request_id'] if cards else None
        browser = browser_form(model, chosen)
        session_locked = bool(model._state.get('session_id'))
        settings = (model._session_settings or {}).get('tools', model._tool_settings) if session_locked else model._tool_settings
        config = [settings['network'], settings['code_execution'], settings['web_search'], settings['search_mode'], '\n'.join(settings['search_domains']),
                  settings['computer_use'], settings['include_screenshots'], settings['tool_search'], settings['programmatic_tool_calling'], settings['functions'],
                  json.dumps(settings['mcp_servers'], ensure_ascii=False, indent=2)]
        return [gr.update(visible=True), gr.update(visible=True), describe_settings(model),
                gr.update(value=next_model, interactive=not busy), gr.update(value=next_reasoning or 'default', choices=REASONING_CHOICES, interactive=not busy),
                gr.update(visible=bool(cards)), gr.update(choices=[((card['request'].get('origin') or card['request'].get('credential_origin') or '网站请求') + ' · ' + card['request_id'], card['request_id']) for card in cards], value=chosen),
                *browser, *ArtifactPanel.values(model), *[gr.update(**({'value':value} if include_config or session_locked else {}), interactive=not busy and not session_locked) for value in config], gr.update(value=tool_availability(settings))] + ([gr.update(visible=True), self.input_value(model, not busy), gr.update(interactive=not busy), model._conversation_id] if hasattr(self, 'input_files') else []) + self.sidebar_values(model) + [model._conversation_id]

    def wrap_predict(self, predict, capability_ui):
        def predict_with_ui(model, inputs, chatbot, use_websearch=False, files=None, reply_language=None, agent_files=None, request: gr.Request = None):
            if _agent(model): files = agent_files
            projected = ((self.render_chat(model, chat), status) for chat, status in predict(model, inputs, chatbot, use_websearch, files, reply_language, request=request))
            for chat, status in _chat_frames(projected):
                yield chat, status, *self.values(model, request=request, include_config=False), *capability_ui.values(model)
            final_chat = gr.update(value=self.render_chat(model, model.chatbot)) if _agent(model) else gr.update()
            yield final_chat, (model._status() if _agent(model) else gr.update()), *self.values(model, request=request, include_config=False), *capability_ui.values(model)
        return predict_with_ui

    def wire(self, current_model, chatbot, status_display, capability_ui=None):
        # These callbacks are Agent-only. The ordinary shared header is untouched.
        status_display = self.activity
        if hasattr(self, 'input_files'):
            def stage_files(model, files, request: gr.Request):
                if not _agent(model): return self.input_value(model)
                model.bind_owner(request)
                # File.change also fires for server renders. A stale response
                # may show an old subset; restore it, never interpret it as a
                # user removing newer attachments.
                with model._lock:
                    update = self.input_value(model)
                    if tuple(str(path) for path in files or []) == model._pending_upload_paths:
                        update.pop('value', None)
                    return update
            self.input_files.change(stage_files, [current_model, self.input_files], [self.input_files], queue=False)
            def remove_files(model, payload, request: gr.Request):
                if not _agent(model): return '', '聊天已变化，附件未移除', self.input_value(model)
                model.bind_owner(request)
                try:
                    removal = json.loads(payload)
                    if not isinstance(removal.get('ids'), list): raise ValueError()
                    message = model.remove_input_ids(removal['ids'], removal.get('target'))
                except Exception as error: message = str(error) if isinstance(error, gr.Error) else '无法移除附件，请重试'
                return '', message, self.input_value(model)
            self.input_remove.click(self.status_callback(remove_files, 1, input_feedback=True), [current_model, self.input_remove_payload],
                                    [self.input_remove_payload, self.input_feedback, self.input_files], queue=False)
            def upload_files(model, files, target, request: gr.Request):
                if not _agent(model): return '聊天已变化，附件未添加', gr.update(value=[]), gr.update()
                model.bind_owner(request)
                try: message = model.add_input_files(files, target)
                except Exception as error: message = str(error)
                return message, gr.update(value=[]), self.input_value(model)
            upload_event = self.input_picker.upload(self.status_callback(upload_files, input_feedback=True), [current_model, self.input_picker, self.input_target],
                                     [self.input_feedback, self.input_picker, self.input_files], queue=False,
                js='(model, files, target) => { window.chuanhuAgentUploadStaging = true; window.chuanhuRefreshSendButton?.(); return [model, files, window.chuanhuAgentUploadTarget || target]; }')
            upload_event.then(None, [], [], queue=False, js='() => { window.chuanhuAgentUploading = false; window.chuanhuAgentUploadStaging = false; window.chuanhuRefreshSendButton?.(); }')
        def choose_settings(model, name, effort, revision, request: gr.Request):
            model.bind_owner(request)
            try: message = model.set_agent_model(name, effort, revision)
            except Exception as error: message = str(error)
            # A response generated before a newer selection may arrive last.
            # Never write selector values back from this asynchronous event.
            return message if message is not None else gr.update()
        choice_js = '''(state, name, effort, unused) => {
            window.chuanhuAgentChoiceRevision = (window.chuanhuAgentChoiceRevision || 0) + 1;
            return [state, name, effort, window.chuanhuAgentChoiceRevision];
        }'''
        for selector in (self.model, self.reasoning):
            selector.input(self.status_callback(choose_settings), [current_model, self.model, self.reasoning, self.choice_revision], [status_display], queue=False, js=choice_js)
        def choose_tools(model, network, code, search, mode, domains, browser, screenshots, discovery, programmatic, functions, mcp, revision, target, request: gr.Request):
            model.bind_owner(request)
            try:
                value = self.config_from_inputs(network, code, search, mode, domains, browser, screenshots, discovery, programmatic, functions, mcp)
                model.stage_agent_tools(value, revision, target)
                return gr.update()
            except Exception as error: return str(error) if not isinstance(error, json.JSONDecodeError) else 'MCP 配置不是有效 JSON'
        tools_js = '''(model, ...values) => {
            window.chuanhuAgentToolRevision = (window.chuanhuAgentToolRevision || 0) + 1;
            values[values.length - 2] = window.chuanhuAgentToolRevision;
            return [model, ...values];
        }'''
        for component in self.config_inputs:
            component.input(self.status_callback(choose_tools), [current_model, *self.config_inputs, self.tool_revision, self.config_target],
                            [status_display], queue=False, js=tools_js)
        def fork(model, request: gr.Request):
            model.bind_owner(request)
            return model.new_session_from_history()
        fork_event = self.fork.click(self.status_callback(fork, 1), [current_model], [chatbot, status_display]).then(self.values, [current_model], self.outputs).then(self.chat_value, [current_model], [chatbot])
        if capability_ui is not None: fork_event.then(capability_ui.values, [current_model], capability_ui.outputs)
        def reconnect(model, request: gr.Request):
            if _agent(model):
                model.bind_owner(request)
                projected = ((self.render_chat(model, chat), status) for chat, status in model.reconnect())
                for chat, status in _chat_frames(projected):
                    if capability_ui is None: yield chat, status
                    else: yield chat, status, *capability_ui.values(model), *self.values(model, request=request, include_config=False)
                # Generator cleanup clears the observer's running state only
                # after its last yield; update the one main Stop/Send pair now.
                if capability_ui is not None:
                    yield gr.update(value=self.render_chat(model, model.chatbot)), model._status(), *capability_ui.values(model), *self.values(model, request=request, include_config=False)
        reconnect_outputs = [chatbot, status_display] + (capability_ui.outputs + self.outputs if capability_ui is not None else [])
        self.reconnect.click(self.status_callback(reconnect, 1), [current_model], reconnect_outputs)
        def retry_file(model, identifier, request: gr.Request):
            model.bind_owner(request)
            for _, status in model.retry_artifact(identifier):
                yield status, *self.values(model, request=request, include_config=False)
        self.artifacts.retry.click(self.status_callback(retry_file), [current_model, self.artifacts.retry_id], [status_display, *self.outputs])
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
            button.click(self.status_callback(origin_callback(decision)), [current_model, self.request_id], [status_display, *self.outputs], queue=False)
        def login(model, identifier, payload, request: gr.Request):
            model.bind_owner(request)
            try:
                response = json.loads(payload)
                message = model.respond_browser(identifier, response)
            except Exception as error:
                message = str(error) if isinstance(error, gr.Error) else '登录提交失败；请重新连接确认当前请求，勿重复提交'
            finally: payload = None
            return '', message, *self.values(model)
        self.login_submit.click(self.status_callback(login, 1), [current_model, self.request_id, self.payload], [self.payload, status_display, *self.outputs], queue=False,
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
            values = [*self.values(model, request=request, include_config=False), self.activity_value(model)]
            if hasattr(self, 'input_feedback'):
                values.append(gr.update() if _agent(model) and model._pending_upload_paths else gr.update(value='', visible=False))
            return values
        chatbot.change(live_values, [current_model], [*self.outputs, self.activity] + ([self.input_feedback] if hasattr(self, 'input_feedback') else []), queue=False, show_progress=False)
