"""Stateful local-only Agent service for browser QA of the production UI.

Keywords: slow, permission, login, files, files-only. All responses and files
are synthetic. Login forms accept dummy values only; no external API is used.
"""
from copy import deepcopy
from pathlib import Path
from threading import Event, RLock, Thread
import json
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4
from optional.agents.tools import submit_browser_response


class MainChatMock:
    def __init__(self):
        self.sessions={}
        self.lock=RLock()

    def snapshot(self, session):
        with self.lock:
            state=self.sessions[session]
            return {'session_id':session,'turn_id':state['turn_id'],'outcome':state['outcome'],
                    'text':state['text'],'items':deepcopy(state['items']),'sync_complete':True,
                    'history_authoritative':True,'required_actions':deepcopy(state['cards']),
                    'settings':{'agent':{'model':state['model'],'reasoning':{'effort':state['reasoning']}},
                                'environment':{'type':'openai_hosted','network':{'access':'enabled' if state['tools']['network'] else 'disabled'}}}}

    def _observe(self, sid):
        previous=None
        while True:
            snapshot=self.snapshot(sid)
            serialized=json.dumps(snapshot,sort_keys=True)
            final=snapshot['outcome'] in ('completed','cancelled','failed')
            if serialized!=previous or final:
                yield {'type':'result' if final else 'progress',**snapshot}
                previous=serialized
            if final:return
            time.sleep(.1)

    def _card(self,sid,kind):
        state=self.sessions[sid]
        request={'type':'browser_origin_access','origin':'https://example.com','reason':'模拟网站访问，仅用于界面验收，不会真的打开网站'}
        if kind=='login':
            request={'type':'browser_authentication','credential_origin':'https://example.com',
                     'reason':'模拟安全登录：仅填写任意虚构测试值，不能填写真实凭据',
                     'fields':[{'id':'email','label':'测试邮箱（虚构）','type':'text','required':True},
                               {'id':'password','label':'测试密码（虚构）','type':'password','required':True}],
                     'options':[{'id':'password','label':'模拟邮箱与密码','field_ids':['email','password']},
                                {'id':'cancel_fields','label':'模拟无需字段的登录方式','field_ids':[]}]}
        with self.lock:
            state['response'].clear();state['decision']=None
            state['cards']=[{'request_id':'request_'+uuid4().hex,'turn_id':state['turn_id'],'request':request}]
            state['outcome']='requires_action'
        while not state['response'].wait(.1):
            if state['cancel'].is_set():return False
        return state['decision'] in ('approve','submit')

    def _execute(self,sid,prompt):
        state=self.sessions[sid]
        try:
            duration=15 if any(word in prompt.lower() for word in ('slow','慢')) else .8
            if state['cancel'].wait(duration):return self._finish(sid,'cancelled','模拟任务已停止')
            wants_login=any(word in prompt.lower() for word in ('login','登录'))
            if wants_login or any(word in prompt.lower() for word in ('permission','授权')):
                if not self._card(sid,'origin'):
                    return self._finish(sid,'cancelled' if state['cancel'].is_set() else 'completed','模拟网站请求已拒绝或取消，未访问网站')
                if wants_login and not self._card(sid,'login'):
                    return self._finish(sid,'cancelled' if state['cancel'].is_set() else 'completed','模拟登录已取消，没有登录成功声明')
            if state['cancel'].is_set():return self._finish(sid,'cancelled','模拟任务已停止')
            with self.lock:state['cards']=[];state['outcome']='in_progress'
            answer='' if 'files-only' in prompt.lower() else '模拟 Agent 回答：'+prompt
            if 'login' in prompt.lower():answer+='\n模拟登录请求已处理，原会话继续运行（没有真实网站或登录）。'
            self._make_files(sid,3 if any(word in prompt.lower() for word in ('files','文件')) else 1)
            self._finish(sid,'completed',answer)
        except Exception:
            self._finish(sid,'failed','模拟器内部错误；没有调用外部 API')

    def _finish(self,sid,outcome,text):
        with self.lock:
            state=self.sessions[sid];state['outcome']=outcome;state['text']=text;state['cards']=[]
            if text:state['items'].append({'id':'msg_'+uuid4().hex,'type':'message','role':'assistant','turn_id':state['turn_id'],'status':'completed','content':[{'type':'output_text','text':text}]})

    def _make_files(self,sid,count):
        state=self.sessions[sid]
        folder=Path(tempfile.mkdtemp(prefix='chuanhu-agent-artifacts-'))
        for index in range(count):
            place=folder/str(index);place.mkdir()
            path=place/('同名文件.txt' if index<2 else '可重试文件.txt');path.write_text('Synthetic artifact '+str(index),encoding='utf-8')
            state['artifacts'].append({'id':'artifact_'+uuid4().hex,'session_id':sid,'turn_id':state['turn_id'],'name':path.name,'path':str(path),'type':'text/plain','size':path.stat().st_size,'status':'ready','fail_once':index==2})

    def worker(self,command):
        action=command['action'];sid=command.get('session_id')
        if action=='run':
            with self.lock:
                if sid is None:
                    sid='sess_synthetic_'+uuid4().hex
                    self.sessions[sid]={'items':[],'artifacts':[],'tools':deepcopy(command['tool_settings']),
                                        'model':command['model'],'reasoning':command.get('reasoning'),'instructions':command.get('instructions')}
                state=self.sessions[sid]
                if state.get('outcome') in ('in_progress','requires_action'):
                    raise ValueError('模拟会话仍在运行；产品发送门应先拒绝重复请求')
                state.update(turn_id='turn_'+uuid4().hex,outcome='in_progress',text='',cards=[],cancel=Event(),response=Event())
                text=command['prompt']
                if command.get('history_reference'):text='历史文字引用：\n'+json.dumps(command['history_reference'],ensure_ascii=False)+'\n本轮新请求：'+text
                state['items'].append({'id':'msg_'+uuid4().hex,'type':'message','role':'user','turn_id':state['turn_id'],'status':'completed','content':[{'type':'input_text','text':text}]})
            Thread(target=self._execute,args=(sid,command['prompt']),daemon=True).start()
            yield from self._observe(sid)
        elif action in ('recover','inspect'):
            if sid not in self.sessions:
                yield {'type':'error','message':'模拟服务没有这个会话','diagnostics':{'status_code':404}};return
            yield from self._observe(sid)
        elif action=='cancel':
            state=self.sessions[sid]
            if state['outcome'] in ('completed','cancelled','failed'):
                yield {'type':'result','outcome':state['outcome']};return
            if state['turn_id']!=command.get('turn_id'):
                yield {'type':'error','message':'模拟请求属于旧轮次'};return
            state['cancel'].set()
            yield {'type':'result','outcome':'cancel_requested','message':'正在确认模拟任务停止'}
        elif action=='update':
            state=self.sessions[sid]
            if state['outcome'] not in ('completed','cancelled','failed'):
                yield {'type':'error','message':'模拟任务运行中不能改参数'};return
            if command['model']=='invalid-model':
                yield {'type':'error','message':'模拟服务不支持此模型，原值保持'};return
            state['model']=command['model'];state['reasoning']=command.get('reasoning')
            yield {'type':'result','settings':{'model':state['model'],'reasoning':state['reasoning']}}
        elif action=='browser_response':
            state=self.sessions[sid]
            def retrieve(identifier):
                return {'status':'requires_action' if state['cards'] else 'idle',
                        'required_actions':[{'type':'computer_use_approval_request',**card} for card in deepcopy(state['cards'])]}
            def submit(identifier,events):
                response=events[0]['response']
                # Do not retain any form values, even in this synthetic service.
                state['decision']=response.get('decision') or response.get('action')
                state['cards']=[];state['response'].set()
            client=SimpleNamespace(beta=SimpleNamespace(agents=SimpleNamespace(sessions=SimpleNamespace(retrieve=retrieve,events=SimpleNamespace(create=submit)))))
            result=submit_browser_response(client,sid,command['turn_id'],command['request_id'],command['response'])
            yield {'type':'result',**result}
        elif action=='download':
            state=self.sessions[sid];selected=[item for item in state['artifacts'] if command.get('artifact_ids') is None or item['id'] in command['artifact_ids']]
            records=[{key:value for key,value in item.items() if key not in ('path','fail_once')}|{'status':'preparing'} for item in selected]
            yield {'type':'progress','artifacts':deepcopy(records)}
            for index,item in enumerate(selected):
                time.sleep(.5)
                if item.pop('fail_once',False):records[index].update(status='failed',error='模拟单文件下载失败，可单独重试')
                else:records[index].update(status='ready',path=item['path'])
                yield {'type':'progress','artifacts':deepcopy(records)}
            yield {'type':'result','artifacts':deepcopy(records)}
        elif action=='title':yield {'type':'result','title':'模拟会话标题'}
        else:yield {'type':'error','message':'模拟器不支持此动作'}
