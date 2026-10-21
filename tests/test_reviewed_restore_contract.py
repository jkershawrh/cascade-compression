"""Qualified evidence is still rejected when its serializer binding changes."""
import hashlib
import json

from tests.test_positive_lifecycle import bridge, activate
from cascade_compression.bridge import CascadeBridge
from cascade_compression.cascade.protocol import Signal


def test_qualified_snapshot_serializer_mismatch_cannot_restore_or_reactivate(bridge, monkeypatch, tmp_path):
    activate(bridge, monkeypatch)
    original_timestamp = bridge._activation_timestamps['routine']
    bridge.memory_archive.store(Signal(signal_type='synthetic-audit-memory', severity='critical'))
    original_size = bridge.memory_archive.size
    bridge._ledger_receipts_written = 7
    assert bridge._save_state()
    raw = open(bridge._state_file, 'rb').read()
    historical = tmp_path / 'historical-qualified-input.json'
    historical.write_bytes(raw)
    (tmp_path / 'historical-qualified-input.sha256').write_text(hashlib.sha256(raw).hexdigest())
    state = json.loads(raw)
    assert state['activation_timestamps']['routine'] == original_timestamp
    state['serializer_revision'] = 'synthetic-mismatched-input-revision'
    with open(bridge._state_file, 'w') as stream:
        json.dump(state, stream)
    again = CascadeBridge()
    try:
        again.corpus_analyzer.maybe_analyze = lambda: []
        assert again.memory_archive.size == original_size
        assert again._ledger_receipts_written == 7
        for _ in range(2):
            assert not again._activated_types
            assert not again._activation_timestamps
            assert not again._qualification_evidence
            again._discover_and_promote()
            assert not again._activated_types
            assert not again._activation_timestamps
            assert not again._qualification_evidence
    finally:
        again.close()
