import asyncio
import gradio as gr
from gradio.state_holder import SessionState
from modules.agent_ui import AgentPanel
from test_agent_model import env,select,send,complete,request


def test_sidebar_uses_session_snapshot_and_restores_ordinary_controls(env,monkeypatch):
    complete(env,monkeypatch);model=select(env)
    with gr.Blocks(analytics_enabled=False) as app:
        current=gr.State();button=gr.Button();chat=gr.Chatbot();status=gr.Markdown()
        with gr.Tab('对话') as tab:
            panel=AgentPanel();panel.selectors();panel.output_components();panel.settings_components()
            with gr.Accordion('Prompt') as prompt_group:
                prompt=gr.Textbox();template=gr.Dropdown(choices=[])
        panel.bind_sidebar(tab,prompt,template,prompt_group,'对话')
        button.click(panel.values,[current],panel.outputs)
    state=SessionState(app);state[current._id]=model
    def frame():return asyncio.run(app.process_api(0,[None],state=state,request=request()))['data']
    first=frame();assert first[panel.outputs.index(tab)]['label']=='Agent'
    assert panel.model.label=='使用模型' and not panel.settings_status.visible and not panel.availability.visible and not panel.fork.visible
    for component in [*panel.config_inputs,prompt,template,panel.save]:assert first[panel.outputs.index(component)]['interactive']
    model._state['outcome']='uncertain';model._needs_sync=True
    unknown=frame()
    for component in [*panel.config_inputs,prompt,template,panel.save]:assert unknown[panel.outputs.index(component)]['interactive'] is False
    model._state['outcome']='not_started';model._needs_sync=False
    send(env,model)
    locked=frame()
    for component in [*panel.config_inputs,prompt,template,panel.save]:assert locked[panel.outputs.index(component)]['interactive'] is False
    for component in [panel.model,panel.reasoning]:assert locked[panel.outputs.index(component)]['interactive'] is True
    assert locked[panel.outputs.index(prompt)]['value']==model._session_settings['instructions']
    ordinary=select(env,model,name='GPT3.5 Turbo');state[current._id]=ordinary;restored=frame()
    assert restored[panel.outputs.index(tab)]['label']=='对话'
    assert restored[panel.outputs.index(prompt)]['interactive'] is True
    app.close()
