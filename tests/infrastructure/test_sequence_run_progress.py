"""Live events must arrive before completion and stay attached to their sequence."""
from threading import Event

from backend.agent import AlignmentRequest
from backend.agent.orchestrator.progress import record_trace
from backend.application.alignment import alignment_wrapper as wrapper


def test_public_decision_event_keeps_evidence_without_private_payload():
    events = []
    trace = []
    request = AlignmentRequest(target_seq='ACGU', status_callback=events.append)
    record_trace(request, trace, {
        'stage': 'planning',
        'agent_decision': {'planner_source': 'llm', 'decision': 'tool',
                           'action_id': 'align_numbering_cm', 'reason_code': 'structure_support'},
        'observation': {'internal_prompt': 'not public'},
        'candidate_reasons': [{'code': 'variable_boundary_ambiguity'}],
    })
    public = events[0]['process_event']
    assert public['source'] == 'agent'
    assert public['action_id'] == 'align_numbering_cm'
    assert public['reasons'] == ['variable_boundary_ambiguity']
    assert 'observation' not in public
    assert trace[0]['observation']['internal_prompt'] == 'not public'


def test_callback_failure_does_not_change_execution():
    def disconnected(_):
        raise RuntimeError('browser disconnected')
    trace = []
    record_trace(AlignmentRequest(target_seq='ACGU', status_callback=disconnected),
                 trace, {'stage': 'tool_started', 'tool': 'align_numbering_cm'})
    assert len(trace) == 1


def test_batch_stream_delivers_indexed_steps_before_final_results(monkeypatch):
    release = Event()
    def fake_alignment(*, target_seq, status_callback, **kwargs):
        status_callback({'stage': 'tool_started', 'process_event': {'stage': 'tool_started', 'tool': target_seq}})
        assert release.wait(timeout=5)
        return 'fake', '1,2\n-,-\nA,C\n', [], {}
    monkeypatch.setattr(wrapper, 'perform_full_alignment_with_meta', fake_alignment)
    stream = wrapper.iter_batch_alignment_events_with_meta(['AC', 'GU'], batch_max_workers=2)
    received = []
    try:
        for event in stream:
            if event['event'] == 'status' and 'index' in event['data']:
                received.append(event['data'])
                if len(received) == 2:
                    break
        assert {x['index'] for x in received} == {0, 1}
        assert all(x['process_event']['tool'] == ['AC', 'GU'][x['index']] for x in received)
    finally:
        release.set()
        remaining = list(stream)
    assert len([x for x in remaining if x['event'] == 'item']) == 2
    assert remaining[-1]['event'] == 'done'
