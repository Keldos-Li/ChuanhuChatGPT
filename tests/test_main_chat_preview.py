"""Exercise the actual main-page preview's wired history callbacks offline."""
from pathlib import Path
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

    async def call(name, inputs):
        index = functions[name]
        if name=='transfer_input' and len(inputs)==2:
            current=state[app.fns[functions['initial']].outputs[0]._id]
            choice=getattr(current,'agent_model_choice',('gpt-6-astra',None))
            inputs=[*inputs,choice[0],choice[1] or 'default',getattr(current,'_choice_revision',0)]
        result = await app.process_api(index, inputs, state=state, request=request)
        while result['is_generating']:
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
        title = await call('auto_name_chat_history', [None, i18n('naming.by_first_question'), None, False])
        saved = model.history_file_path
        assert saved == 'Ordinary  title.json'
        assert any(choice[0] == 'Ordinary  title' for choice in title[0]['choices'])
        reset = await call('reset', [None, False])
        assert reset[0] == [] and model.history == []
        restored = await call('load_chat_history', [None, saved[:-5]])
        assert restored[2]['value'][0][0] == 'Ordinary: title!'

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
        await call('save', [None, False, True, True, 'live', '', True, False,
                            True, True, ['text_statistics'], '[]'])
        await call('fork', [None])
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
    finally:
        app.close()


if __name__ == '__main__':
    import asyncio
    asyncio.run(exercise())
