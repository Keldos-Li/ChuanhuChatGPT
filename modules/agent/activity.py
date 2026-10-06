"""Local observation clocks; never infer server execution or private reasoning."""
import time
import math

KINDS = frozenset({'reasoning', 'function_call', 'function_call_output', 'mcp_call',
    'command_execution', 'web_search_call', 'computer_use_call', 'computer_use_approval_request',
    'computer_use_approval_request_result', 'create_subagent_call', 'send_subagent_input_call',
    'resume_subagent_call', 'wait_for_subagents_call', 'interrupt_subagent_call',
    'close_subagent_call', 'agent_message'})


class ActivityClock:
    def __init__(self):
        self.records = {}

    def observe(self, item, *, first=False, phase=None):
        key, turn = item.get('id'), item.get('turn_id')
        if not isinstance(key, str) or not isinstance(turn, str) or item.get('type') not in KINDS:
            return
        record = self.records.get((turn, key))
        if record is None:
            record = dict(item_id=key, turn_id=turn, start=time.monotonic() if first else None, end=None,
                          phase='running')
            self.records[(turn, key)] = record
        if record['end'] is not None: return
        if phase=='running' and item['type']=='function_call' and record['start'] is None:
            record['start']=time.monotonic()
        status = item.get('status')
        if phase is None:
            phase = ('failed' if item.get('error') else 'waiting' if item['type']=='function_call' and status=='completed' else
                     'completed' if item['type']=='function_call_output' else
                     'waiting' if item['type']=='computer_use_approval_request' else
                     status if status in ('completed','failed','incomplete','cancelled') else 'running')
        record['phase'] = phase
        if phase in ('completed','failed','incomplete','cancelled'):
            record['end'] = time.monotonic()

    def finish_turn(self, turn, outcome):
        for (source_turn, _), record in self.records.items():
            if source_turn == turn and record['end'] is None:
                record['end'] = time.monotonic()
                record['phase'] = {'cancelled':'turn_cancelled','failed':'turn_failed'}.get(outcome,'incomplete')

    def snapshot(self):
        now = time.monotonic()
        return [dict(item_id=record['item_id'], turn_id=record['turn_id'], phase=record['phase'],
                     elapsed_ms=None if record['start'] is None else max(0, round(((record['end'] or now)-record['start'])*1000)),
                     running=record['end'] is None, sampled_at=now)
                for record in self.records.values()]


def safe_records(values):
    if not isinstance(values,list):return []
    records=[]
    for value in values[:20000]:
        if not isinstance(value,dict):continue
        if any(not isinstance(value.get(key),str) or not value[key] or len(value[key])>512 for key in ('item_id','turn_id')):continue
        phase=value.get('phase')
        if phase not in ('running','waiting','completed','failed','incomplete','cancelled','turn_cancelled','turn_failed'):continue
        elapsed=value.get('elapsed_ms');sampled=value.get('sampled_at')
        if not (type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=31536000000):elapsed=None
        if not (type(sampled) in (int,float) and math.isfinite(sampled) and 0<=sampled<=time.monotonic()+1):sampled=None
        records.append(dict(item_id=value['item_id'],turn_id=value['turn_id'],phase=phase,elapsed_ms=elapsed,
                            sampled_at=sampled,running=value.get('running') is True and phase in ('running','waiting')))
    return records
