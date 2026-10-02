"""Exercise the actual main-page preview's wired history callbacks offline."""
from pathlib import Path
import json
import subprocess
import sys


def test_preview_send_title_reset_and_history_callbacks():
    result = subprocess.run([sys.executable, __file__, '--exercise'],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


async def exercise():
    import gradio as gr
    from gradio.state_holder import SessionState
    from main_chat_preview import build
    app = build()
    from modules.presets import i18n
    state = SessionState(app)
    functions = {fn.fn.__name__: i for i, fn in enumerate(app.fns) if fn.fn}
    request = gr.Request(session_hash='preview-history')
    chat_frames = []

    async def call(name, inputs):
        index = functions[name]
        if name=='transfer_input' and len(inputs)==2:
            current=state[app.fns[functions['initial']].outputs[0]._id]
            choice=getattr(current,'agent_model_choice',('gpt-6-astra',None))
            file_values=[{'path':path,'orig_name':Path(path).name,'meta':{'_type':'gradio.FileData'}} for path in getattr(current,'_pending_upload_paths',())]
            inputs=[*inputs,choice[0],choice[1] or 'default',getattr(current,'_choice_revision',0),file_values]
            if getattr(current,'is_hosted_agent',False):
                tools=current._current_settings()['tools']
                inputs.extend([tools['network'],tools['code_execution'],tools['web_search'],tools['search_mode'],'\n'.join(tools['search_domains']),
                    tools['computer_use'],tools['include_screenshots'],tools['tool_search'],tools['programmatic_tool_calling'],tools['functions'],
                    json.dumps(tools['mcp_servers']),current.system_prompt,current._conversation_id,current._tool_revision])
            else: inputs.extend(component.value for component in app.fns[index].inputs[6:])
        if name=='predict_with_ui' and len(inputs)==6: inputs=[*inputs,[]]
        result = await app.process_api(index, inputs, state=state, request=request)
        while True:
            if name == 'predict_with_ui':
                frame = result['data'][0]
                if isinstance(frame, list) or (isinstance(frame, dict) and 'value' in frame):
                    chat_frames.append(frame)
            if not result['is_generating']: break
            result = await app.process_api(index, inputs, state=state, request=request,
                                           iterator=result['iterator'])
        return result['data']

    try:
        await call('initial', [])
        current_id = app.fns[functions['initial']].outputs[0]._id
        model = state[current_id]
        # Ordinary automatic titles previously failed on a missing real helper.
        await call('transfer_input', ['Ordinary: title!', None])
        await call('predict_with_ui', [None, None, [], False, [], 'English'])
        ordinary_chat = chat_frames[-1]
        if isinstance(ordinary_chat, dict): ordinary_chat = ordinary_chat['value']
        assert 'class="raw-message hideM"' in ordinary_chat[0][1]
        assert 'class="md-message"' in ordinary_chat[0][1]
        assert 'Ordinary synthetic response' in ordinary_chat[0][1]
        title = await call('auto_name_chat_history', [None, i18n('naming.by_first_question'), None, False])
        saved = model.history_file_path
        assert saved == 'Ordinary  title.json'
        assert any(choice[0] == 'Ordinary  title' for choice in title[0]['choices'])
        reset = await call('reset', [None, False])
        assert reset[0] == [] and model.history == []
        restored = await call('load_chat_history', [None, saved[:-5]])
        assert model.chatbot[0][0] == 'Ordinary: title!'
        assert 'class="user-message"' in restored[2]['value'][0][0]
        assert 'class="raw-message hideM"' in restored[2]['value'][0][1]

        await call('change_model', ['OpenAI Agent', None, '', 1, 1, 'QA', '', None])
        model = state[current_id]
        await call('reset', [None, False])
        await call('transfer_input', ['files', None])
        await call('predict_with_ui', [None, None, model.chatbot, False, [], 'English'])
        await call('auto_name_chat_history', [None, i18n('naming.by_first_question'), None, False])
        saved = model.history_file_path
        session = model._state['session_id']
        assert len(model._artifacts) == 3
        await call('reset', [None, False])
        assert not model._state.get('session_id') and not model._artifacts
        await call('load_chat_history', [None, saved.removesuffix('.json')])
        assert model._state['session_id'] == session
        await call('reconnect', [None])
        assert len(model._artifacts) == 3
        assert model.chatbot[-1][0] == 'files'
        # The browser's actual new-settings flow must reconcile the provisional
        # local row with the authoritative cloud message without a blank tail.
        await call('fork', [None])
        await call('choose_tools', [None, False, True, True, 'live', '', True, False,
                            True, True, ['text_statistics'], '[]', 1, model._conversation_id])
        await call('transfer_input', ['new-settings-followup', None])
        await call('predict_with_ui', [None, None, model.chatbot, False, [], 'English'])
        assert len(model.chatbot) == 1, model.chatbot
        assert model.chatbot[0][1] == '模拟 Agent 回答：new-settings-followup'
        assert len(model.history) == 2, model.history
        assert model._session_settings['tools']['network'] is False
        await call('change_model', ['GPT3.5 Turbo', None, '', 1, 1, 'QA', '', None])
        ordinary = state[current_id]
        assert ordinary.chatbot == model.chatbot
        assert len(ordinary.chatbot) == 1 and ordinary.chatbot[0][1]
        await call('change_model', ['OpenAI Agent', None, '', 1, 1, 'QA', '', None])
        await call('reconnect',[None])
        await call('reset',[None,False])
        from uuid import uuid4
        upload=Path(gr.utils.get_upload_folder())/('qa-'+uuid4().hex)
        upload.mkdir(parents=True);path=upload/'synthetic.csv';path.write_text('day,value\n1,10\n2,20')
        data=[{'path':str(path),'orig_name':path.name,'meta':{'_type':'gradio.FileData'}}]
        model=state[current_id]
        await call('upload_files',[None,data,model._conversation_id])
        assert model._pending_upload_paths
        await call('transfer_input',['',None])
        await call('predict_with_ui',[None,None,[],False,[],'English',data])
        assert 'synthetic.csv' in model.chatbot[0][0]
        assert '/workspace/inputs/' not in model.chatbot[0][0] and not model._pending_upload_paths
    finally:
        app.close()


if __name__ == '__main__':
    import asyncio
    asyncio.run(exercise())
