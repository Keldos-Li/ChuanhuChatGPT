"""Production terminal/worker/model/file boundaries, without provider calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
from types import SimpleNamespace
import pytest
import gradio as gr
from agent_fixtures import env, select, request
from modules.agent.runtime import TurnState

ROOT=Path(__file__).resolve().parents[2]
def event(kind, **fields):
    return dict(type='agent.session.turn.'+kind, event_id='evt_'+kind, session_id='sess_one', turn_id='turn_one', **fields)
def item(identifier='answer', text='answer', index=0):
    return dict(id=identifier, type='message',role='assistant',turn_id='turn_one',status='completed',content=[dict(type='output_text',text=text)])
def complete_state():
    state=TurnState()
    turn=dict(id='turn_one',session_id='sess_one',agent_id='agent_one',status='in_progress')
    state.accept(event('created',turn=turn))
    state.accept(event('item.done',output_index=0,item=item()))
    state.accept(event('completed',turn=dict(turn,status='completed')))
    return state

def wait_main(model):
    task=model._background_task
    task.thread.join(5)
    assert not task.thread.is_alive() and task.error is None
    return task

def wait_files(model):
    from modules.agent.file_jobs import FILE_JOBS
    limit=time.monotonic()+8
    while FILE_JOBS.queue.unfinished_tasks and time.monotonic()<limit: time.sleep(.01)
    assert not FILE_JOBS.queue.unfinished_tasks
    model.refresh_files(resume=False)

def test_turn_receipt_is_partial_session_and_requires_complete_items():
    state=complete_state()
    assert state.turn_complete and state.snapshot()['capture']['items']=='partial' and state.sync_complete is None
    state.final_items.clear();assert not state.turn_complete
    state=complete_state();state.output_order['answer']=1;assert not state.turn_complete
    state=complete_state();state.items['unfinished']=dict(item('unfinished'),status='in_progress');assert not state.turn_complete
    state=complete_state();state.stream_gap=True;assert not state.turn_complete

@pytest.mark.parametrize('case',['healthy','slow_close','slow_list','slow_content','incomplete'])
def test_real_worker_sdk_ipc_main_queue_boundary(env,monkeypatch,tmp_path,case):
    from modules.agent import transport
    trace=tmp_path/'trace.jsonl'
    real_popen=subprocess.Popen
    processes=[]
    def popen(args,**kwargs):
        command=[sys.executable,str(Path(__file__).with_name('offline_finalization_worker.py')),str(ROOT),case,str(trace),str(tmp_path)]
        process=real_popen(command,**kwargs);processes.append(process);return process
    monkeypatch.setattr(transport.subprocess,'Popen',popen)
    monkeypatch.setattr(env.agents,'worker_messages',transport.worker_messages)
    model=select(env)
    stream=env.wrappers['predict'](model,'Synthetic prompt',[],request=request())
    next(stream); task=wait_main(model); completed=time.monotonic()
    assert not model._running and not model._needs_sync and not model._background_busy
    model._assert_idle()
    assert model.history==[dict(role='user',content='Synthetic prompt'),dict(role='assistant',content='Canonical answer')]
    binding=model._store().get(model._owner,model.history_file_path)
    assert binding['state']['outcome']=='completed'
    events=[json.loads(line) for line in trace.read_text().splitlines()]
    if case!='incomplete':
        assert not any(e['event'].startswith('GET_') for e in events)
        result=next(e['time'] for e in events if e['event']=='result')
        assert completed-result<1.0
        assert not any(e['event']=='EOF_requested' for e in events)
        assert model._transcript['capture']['items']=='partial'
    else:
        assert any(e['event']=='GET_items' for e in events)
        assert completed-next(e['time'] for e in events if e['event']=='terminal')>=1.8
        assert model._transcript['capture']['items']=='complete'
    if case=='slow_content': assert not any(e['event']=='content_done' for e in events)
    wait_files(model);stream.close()
    if os.environ.get('FINALIZATION_EVIDENCE_DIR'):
        folder=Path(os.environ['FINALIZATION_EVIDENCE_DIR']);folder.mkdir(parents=True,exist_ok=True)
        events=[json.loads(line) for line in trace.read_text().splitlines()]
        receipt=dict(case=case,synthetic=True,main_queue_end=completed,events=events,
            terminal_to_queue_end_ms=round(1000*(completed-next(e['time'] for e in events if e['event']=='terminal')),3),
            get_requested=any(e['event'].startswith('GET_') for e in events),
            eof_requested=any(e['event']=='EOF_requested' for e in events),
            turn_capture=model._transcript['capture']['items'])
        (folder/(case+'.json')).write_text(json.dumps(receipt,indent=2))
    if case=='slow_content': assert model._artifacts[0]['status']=='ready'
    for process in processes:
        if process.poll() is None: process.terminate()


def test_old_turn_file_job_does_not_overwrite_new_run_or_binding(env,monkeypatch,tmp_path):
    ready,release,new_ready,new_release=Event(),Event(),Event(),Event()
    folder=Path(__import__('tempfile').mkdtemp(prefix='chuanhu-agent-artifacts-'))
    file=folder/'old.txt';file.write_text('old')
    calls=[]
    def worker(command):
        calls.append((command['action'],command.get('turn_id')))
        if command['action']=='run':
            if command['prompt']=='first':
                state=complete_state();state.submission_started=True
                yield dict(type='result',**state.snapshot())
            else:
                yield dict(type='progress',session_id='sess_one',turn_id='turn_two',outcome='in_progress',submission_started=True,text='new partial')
                new_ready.set();assert new_release.wait(5)
                yield dict(type='result',session_id='sess_one',turn_id='turn_two',outcome='completed',text='new final')
        elif command['action']=='download':
            if command['turn_id']=='turn_one':
                ready.set();assert release.wait(5)
                yield dict(type='result',artifacts=[dict(id='old_file',session_id='sess_one',turn_id='turn_one',name=file.name,path=str(file),status='ready',size=3),dict(id='future_file',session_id='sess_one',turn_id='turn_two',name='future.txt',status='preparing')])
            else: yield dict(type='result',artifacts=[])
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    model=select(env);first=env.wrappers['predict'](model,'first',[],request=request());next(first);wait_main(model);assert ready.wait(3)
    old_generation=model._state['generation']
    second=env.wrappers['predict'](model,'second',model.chatbot,request=request());next(second);assert new_ready.wait(3)
    current=deepcopy(model._state);release.set();wait_files(model)
    binding=model._store().get(model._owner,model.history_file_path)
    assert binding['state']['generation']==current['generation'] and binding['state']['turn_id']=='turn_two' and binding['state']['outcome']=='in_progress'
    assert binding['artifacts'][0]['turn_id']=='turn_one' and len(binding['artifacts'])==1
    model.auto_save(model.chatbot)
    assert model._state==current and old_generation!=current['generation']
    document=json.loads((env.history_dir/model.history_file_path).read_text())
    assert document['history'][-1]['content']=='new partial'
    assert document['agent_transcript']['files'][0]['source_id']=='old_file'
    new_release.set();wait_main(model);wait_files(model);first.close();second.close()


def test_terminal_persistence_failure_blocks_send_and_slot_release(env,monkeypatch):
    from modules.agent import tasks
    model=select(env)
    def worker(command):
        state=complete_state();state.submission_started=True
        yield dict(type='result',**state.snapshot())
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    from modules.models.base_model import BaseLLMModel
    save=BaseLLMModel.auto_save
    def failing_save(self,chat=None):
        if self is model and model._state.get('outcome')=='completed':
            raise OSError('synthetic disk failure')
        return save(self,chat)
    monkeypatch.setattr(BaseLLMModel,'auto_save',failing_save)
    stream=env.wrappers['predict'](model,'first',[],request=request());next(stream)
    task=model._background_task;task.thread.join(5)
    assert task.done and task.error and model._state.get('persistence_failed')
    with pytest.raises(gr.Error):model._assert_idle()
    assert tasks.TASKS.find(model) is None
    stream.close()


def test_background_file_json_save_cannot_extend_main_completion(env,monkeypatch,tmp_path):
    from modules.agent import file_jobs
    entered,release=Event(),Event()
    folder=Path(__import__('tempfile').mkdtemp(prefix='chuanhu-agent-artifacts-'))
    file=folder/'file.txt';file.write_text('bytes')
    atomic=file_jobs._atomic_document
    def slow_file_save(path,document):
        entered.set();assert release.wait(5);return atomic(path,document)
    monkeypatch.setattr(file_jobs,'_atomic_document',slow_file_save)
    submit=file_jobs.FILE_JOBS.submit
    def submit_before_final(*args,**kwargs):
        value=submit(*args,**kwargs);assert entered.wait(3);return value
    monkeypatch.setattr(file_jobs.FILE_JOBS,'submit',submit_before_final)
    def worker(command):
        if command['action']=='run':
            state=complete_state();state.submission_started=True;yield dict(type='result',**state.snapshot())
        else:yield dict(type='result',artifacts=[dict(id='file',session_id='sess_one',turn_id='turn_one',name=file.name,size=5,path=str(file),status='ready')])
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    model=select(env);stream=env.wrappers['predict'](model,'first',[],request=request());next(stream)
    try:
        wait_main(model);model._assert_idle()
        assert not release.is_set()
        assert model._store().get(model._owner,model.history_file_path)['local_phase']=='settled'
        model.refresh_files(resume=False)
        assert model._artifacts[0]['status']=='ready'
    finally:release.set();wait_files(model);stream.close()


def test_slow_main_terminal_save_still_blocks_send(env,monkeypatch):
    entered,release=Event(),Event();model=select(env)
    save=model.auto_save
    def slow_main_save(chat=None):
        if model._state.get('outcome')=='completed':entered.set();assert release.wait(5)
        return save(chat)
    monkeypatch.setattr(model,'auto_save',slow_main_save)
    def worker(command):
        if command['action']=='run':
            state=complete_state();state.submission_started=True;yield dict(type='result',**state.snapshot())
        else:yield dict(type='result',artifacts=[])
    monkeypatch.setattr(env.agents,'worker_messages',worker)
    stream=env.wrappers['predict'](model,'first',[],request=request());next(stream);assert entered.wait(3)
    try:
        assert not model._background_task.done
        with pytest.raises(gr.Error):model._assert_idle()
    finally:release.set();wait_main(model);wait_files(model);stream.close()
