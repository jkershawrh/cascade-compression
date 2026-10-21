"""Synthetic outcomes flow through production classification and promotion paths."""
import json
import time
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.promotion import AgentMetrics, RuleAgent
from cascade_compression.cascade.protocol import Signal, Outcome
from cascade_compression.classifier import ClassificationResult


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv('CASCADE_STATE_FILE', str(tmp_path/'state.json'))
    b = CascadeBridge()
    b.corpus_analyzer.maybe_analyze = lambda: []
    m = AgentMetrics(name='synthetic_rule', config={'signal_type':'routine','pattern_type':'dominant_type'})
    b._agent_metrics[m.name] = m
    b._agent_rules[m.name] = RuleAgent({'name':m.name,'signal_types':['routine'],
        'condition':{'field':'value','operator':'lt','value':10}})
    yield b
    b.close()


def classify(b, monkeypatch, count, label='routine_noise', namespace='prod'):
    # Synthetic backend exercises the real queue, worker, classification result,
    # qualification hook, outcome accounting, and snapshot code. No provider.
    monkeypatch.setattr(b.classifier, 'classify', lambda *args: ClassificationResult(
        label=label, backend='synthetic', authoritative_backend='synthetic', model_revision='synthetic'))
    signals = [Signal(signal_type='routine', severity='low', namespace=namespace,
                      source='synthetic-host', content={'value':i}) for i in range(count)]
    assert b._enqueue_llm_signals(signals)
    monkeypatch.setattr(b, '_classification_dispatch_enabled', lambda: True)
    b._dispatch_llm_batches()
    deadline = time.monotonic() + 5
    while b._llm_active_threads and time.monotonic() < deadline:
        time.sleep(0.005)
    assert b._llm_active_threads == 0
    assert not b._classifier_inflight


def activate(b, monkeypatch):
    classify(b, monkeypatch, 199)
    b._discover_and_promote()
    assert not b._activated_types
    classify(b, monkeypatch, 1)
    b._discover_and_promote()
    assert b._activated_types == {'routine'}
    assert b._agent_metrics['synthetic_rule'].tier == 'nano'
    assert any(e.get('event')=='activated' for e in b._promotion_log)


def test_legitimate_learning_promotes_and_suppresses(bridge, monkeypatch):
    activate(bridge, monkeypatch)
    signals = [Signal(signal_type='routine', severity='low',source='new-host',content={'value':i}) for i in (1,99)]
    decisions = bridge._noise_suppressor.process(signals)
    assert len(decisions) == 1 and decisions[0].outcome == Outcome.SUPPRESS


def test_qualified_roundtrip_preserves_kind_timestamp_and_real_evidence(bridge, monkeypatch):
    activate(bridge, monkeypatch)
    timestamp = bridge._activation_timestamps['routine']
    assert bridge._save_state()
    again = CascadeBridge()
    try:
        assert again._activated_patterns == {'routine':'dominant_type'}
        assert again._activation_timestamps['routine'] == timestamp
        record = again._qualification_evidence['agent:synthetic_rule']
        assert record['noise'] == 200 and record['important'] == 0
        again._check_activation_ttl()
        assert again._activated_types == {'routine'}
    finally:
        again.close()


def test_human_gate_legitimate_approval_roundtrips(bridge, monkeypatch):
    bridge._human_gate = True
    bridge.promotion.human_gate_enabled = True
    classify(bridge, monkeypatch, 200)
    bridge._discover_and_promote()
    metrics = bridge._agent_metrics['synthetic_rule']
    assert metrics.tier == 'pending_approval' and not bridge._activated_types
    bridge.promotion.approve(metrics, approved_by='synthetic-reviewer')
    bridge._discover_and_promote()
    assert bridge._activated_types == {'routine'}
    assert bridge._save_state()
    monkeypatch.setenv('CASCADE_HUMAN_GATE','1')
    again = CascadeBridge()
    try:
        assert again._activated_types == {'routine'}
        assert again._agent_metrics['synthetic_rule'].human_approved_by == 'synthetic-reviewer'
    finally:
        again.close()


def test_model_change_with_valid_saved_evidence_rejects_restore(bridge, monkeypatch):
    activate(bridge, monkeypatch)
    bridge._save_state()
    monkeypatch.setenv('CASCADE_MICRO_MODEL','other-synthetic-model')
    again = CascadeBridge()
    try:
        assert not again._activated_types
    finally:
        again.close()


def test_rule_mutation_requires_200_new_classifications(bridge, monkeypatch):
    activate(bridge, monkeypatch)
    bridge._agent_rules['synthetic_rule'].condition['value'] = 20
    bridge._discover_and_promote()
    assert not bridge._activated_types
    classify(bridge, monkeypatch, 199)
    bridge._discover_and_promote()
    assert not bridge._activated_types
    classify(bridge, monkeypatch, 1)
    bridge._discover_and_promote()
    assert bridge._activated_types == {'routine'}


def test_negative_classification_instantly_demotes(bridge, monkeypatch):
    activate(bridge, monkeypatch)
    classify(bridge, monkeypatch, 1, 'needs_attention')
    assert not bridge._activated_types
    assert bridge._agent_metrics['synthetic_rule'].tier == 'draft'
    assert bridge._fn_count == 0  # Oracle disagreement is not confirmed field truth.
    bridge._save_state()
    again = CascadeBridge()
    try:
        assert not again._activated_types
        assert again._agent_metrics['synthetic_rule'].deactivated
    finally:
        again.close()


def test_ttl_expires_then_accepts_fresh_requalification(bridge, monkeypatch):
    activate(bridge, monkeypatch)
    bridge._activation_timestamps['routine'] = (datetime.now(timezone.utc)-timedelta(hours=73)).isoformat()
    bridge._check_activation_ttl()
    assert not bridge._activated_types
    classify(bridge, monkeypatch, 200)
    bridge._discover_and_promote()
    assert bridge._activated_types == {'routine'}


def test_contextual_learning_still_qualifies(bridge, monkeypatch):
    bridge._agent_metrics.clear(); bridge._agent_rules.clear()
    classify(bridge, monkeypatch, 200, namespace='synthetic-context')
    bridge._discover_and_promote()
    assert bridge._activated_contexts['routine'] == {'synthetic-context'}
    stamp = bridge._context_activation_timestamps['routine:synthetic-context']
    assert bridge._save_state()
    again = CascadeBridge()
    try:
        assert again._activated_contexts['routine'] == {'synthetic-context'}
        assert again._context_activation_timestamps['routine:synthetic-context'] == stamp
    finally:
        again.close()
    bridge._check_activation_ttl()
    assert bridge._activated_contexts['routine'] == {'synthetic-context'}


def test_expired_context_cannot_restart_and_requalify_from_old_counts(bridge, monkeypatch):
    bridge._agent_metrics.clear(); bridge._agent_rules.clear()
    classify(bridge, monkeypatch, 200, namespace='synthetic-context')
    bridge._discover_and_promote()
    bridge._context_activation_timestamps['routine:synthetic-context'] = (
        datetime.now(timezone.utc)-timedelta(hours=73)).isoformat()
    bridge._save_state()
    again = CascadeBridge()
    try:
        again.corpus_analyzer.maybe_analyze = lambda: []
        again._discover_and_promote()
        assert not again._activated_contexts.get('routine')
    finally:
        again.close()


@pytest.mark.parametrize('update', ['missing_timestamp','important','rule_binding'])
def test_real_snapshot_corruption_cannot_reactivate_on_discovery(bridge, monkeypatch, update):
    activate(bridge, monkeypatch)
    bridge._save_state()
    with open(bridge._state_file) as stream:
        saved = json.load(stream)
    if update == 'missing_timestamp':
        saved['activation_timestamps'].clear()
    elif update == 'important':
        saved['llm_important_counts']['routine'] = 1
    else:
        saved['candidate_agents'][0]['rule']['condition']['value'] = 999
    with open(bridge._state_file,'w') as stream:
        json.dump(saved,stream)
    again = CascadeBridge()
    try:
        again.corpus_analyzer.maybe_analyze = lambda: []
        assert not again._activated_types
        again._discover_and_promote()
        assert not again._activated_types
    finally:
        again.close()


def test_inflight_snapshot_restart_receipt_tracks_actual_dispatched_id(bridge, monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    def backend(*args):
        started.set()
        assert release.wait(5)
        return ClassificationResult(label='routine_noise', authoritative_backend='synthetic')
    monkeypatch.setattr(bridge.classifier,'classify',backend)
    monkeypatch.setattr(bridge,'_classification_dispatch_enabled',lambda:True)
    signal = Signal(signal_type='routine',severity='low')
    assert bridge._enqueue_llm_signals([signal])
    bridge._dispatch_llm_batches()
    try:
        assert started.wait(5)
        snapshot = tmp_path/'interrupted-copy.json'
        with open(bridge._state_file,'rb') as stream:
            snapshot.write_bytes(stream.read())
        monkeypatch.setenv('CASCADE_STATE_FILE',str(snapshot))
        again = CascadeBridge()
        try:
            receipt, = again._classifier_terminal_receipts
            assert receipt['signal_id'] == str(signal.signal_id)
            assert receipt['phase'] == 'inflight' and receipt['outcome'] == 'failed'
            assert again._llm_accounting_failed == 1 and again._llm_accounting_succeeded == 0
            assert again.get_stats()['llm_signal_outcomes_since_start']['reconciled']
        finally:
            again.close()
    finally:
        release.set()
        deadline = time.monotonic()+5
        while bridge._llm_active_threads and time.monotonic()<deadline:
            time.sleep(0.005)
        assert bridge._llm_active_threads == 0
def test_pending_restart_has_real_per_item_terminal_receipts(bridge):
    signals = [Signal(signal_type='synthetic',severity=severity) for severity in ('low','high')]
    assert bridge._enqueue_llm_signals(signals)
    again = CascadeBridge()
    try:
        receipts = again._classifier_terminal_receipts
        assert {r['signal_id'] for r in receipts} == {str(s.signal_id) for s in signals}
        assert all(r['reason']=='restart_interrupted' and r['outcome']=='dropped' for r in receipts)
        assert again._llm_accounting_succeeded == 0
        assert again._llm_accounting_dispatched == 0
        assert again._llm_accounting_dropped == 2
        assert again.get_stats()['llm_signal_outcomes_since_start']['reconciled']
    finally:
        again.close()
    third = CascadeBridge()
    try:
        assert len(third._classifier_terminal_receipts) == 2
        assert third._llm_accounting_dropped == 2
    finally:
        third.close()


def test_failed_replace_preserves_snapshot_and_is_observable(bridge):
    assert bridge._save_state()
    before = open(bridge._state_file,'rb').read()
    with patch('cascade_compression.lifecycle.os.replace', side_effect=OSError('synthetic storage failure')):
        assert bridge._save_state() is False
    assert not bridge._state_healthy
    assert open(bridge._state_file,'rb').read() == before
    assert bridge._save_state()
    assert bridge._state_healthy


def test_restart_receipt_retention_is_bounded_and_evictions_visible(bridge, monkeypatch):
    signals = [Signal(signal_type='synthetic',severity='low') for _ in range(3)]
    bridge._enqueue_llm_signals(signals)
    monkeypatch.setenv('CASCADE_CLASSIFIER_TERMINAL_RECEIPTS_MAX','2')
    again = CascadeBridge()
    try:
        assert len(again._classifier_terminal_receipts) == 2
        assert again._classifier_terminal_evicted == 1
        assert again._llm_accounting_dropped == 3
        assert again._llm_accounting_succeeded == 0
        assert again.get_stats()['classifier_restart_terminal_receipts_evicted'] == 1
    finally:
        again.close()


def test_context_evidence_capacity_eviction_withdraws_old_authority(bridge, monkeypatch):
    bridge._CONTEXT_MAP_MAX = 2
    bridge._agent_metrics.clear(); bridge._agent_rules.clear()
    for namespace in ('first','second','third'):
        classify(bridge, monkeypatch,200,namespace=namespace)
        bridge._discover_and_promote()
    assert len([k for k in bridge._qualification_evidence if k.startswith('context:')]) == 2
    assert 'first' not in bridge._activated_contexts['routine']
    assert bridge._activated_contexts['routine'] == {'second','third'}


@pytest.mark.parametrize('profile',['verified_repeat_only','triage_only'])
def test_bounded_profile_does_not_use_learned_type_authority(bridge, monkeypatch, profile):
    # Qualified adaptive learning still exists, but bounded policies do not
    # acquire its broader suppression authority by registration side effects.
    activate(bridge, monkeypatch)
    monkeypatch.setenv('CASCADE_NANO_PROFILE',profile)
    other = CascadeBridge()
    try:
        signals = [Signal(signal_type='routine',severity='low',content={'value':i}) for i in (1,99)]
        assert other.pipeline.run(signals).remaining == signals
    finally:
        other.close()
