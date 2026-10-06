"""Integer tool labels preserve raw millisecond precision and clock semantics."""
from copy import deepcopy
import re
import pytest
from modules.agent import transcript
from modules.agent.transcript_view import _activity, _turn_elapsed


def entry(**extra):
    return transcript.normalize([dict(id='cmd',type='command_execution',turn_id='t1',status='in_progress',command='pwd',**extra)],scope_id='scope')['timeline'][0]


@pytest.mark.parametrize('elapsed,expected',[(0,'0s'),(.1,'0s'),(999,'0s'),(999.75,'0s'),(1000,'1s'),(1000.1,'1s'),(1999.9,'1s'),(2000,'2s'),(31536000000,'31536000s')])
def test_display_floors_seconds_and_keeps_fractional_millisecond_baseline(elapsed,expected):
    clock={'cmd':dict(phase='running',elapsed_ms=elapsed,running=False)};saved=deepcopy(clock)
    markup=_activity([entry()],clock=clock,active=True)
    assert f'data-elapsed-ms="{elapsed}"' in markup and f'>{expected}</span>' in markup
    assert clock==saved


@pytest.mark.parametrize('elapsed',[None,-1,float('nan'),float('inf'),31536000001,False])
def test_unreliable_values_keep_missing_timer_without_inventing_zero(elapsed):
    assert 'agent-activity-elapsed' not in _activity([entry()],clock={'cmd':dict(phase='running',elapsed_ms=elapsed,running=False)},active=True)


@pytest.mark.parametrize('phase',['completed','failed','turn_failed','cancelled','turn_cancelled','incomplete'])
def test_terminal_and_unconfirmed_recovered_timers_remain_frozen_at_floored_value(phase):
    markup=_activity([entry()],clock={'cmd':dict(phase=phase,elapsed_ms=999.75,running=False)},active=True)
    assert '>0s</span>' in markup and 'data-running="false"' in markup and 'data-elapsed-ms="999.75"' in markup


def test_native_precise_duration_uses_its_actual_value_not_rounded_millisecond():
    markup=_activity([entry(duration_ms=999.75)],clock={'cmd':dict(phase='completed',elapsed_ms=2000,running=False)})
    assert 'data-elapsed-ms="999.75"' in markup and '>0s</span>' in markup


def test_live_snapshot_elapsed_still_uses_actual_monotonic_offset(monkeypatch):
    monkeypatch.setattr('modules.agent.transcript_view.time.monotonic',lambda:10.0003)
    clock={'cmd':dict(phase='running',elapsed_ms=999.75,running=True,sampled_at=10)}
    markup=_activity([entry()],clock=clock,active=True)
    assert 'data-running="true"' in markup and '>1s</span>' in markup
    # Static whole-turn duration is outside this approved tool-readout scope.
    assert '2.0s' in _turn_elapsed(dict(status='completed',started_at=100,completed_at=102))
