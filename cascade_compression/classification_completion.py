"""Bounded result retention and atomic local memory/audit completion.

Inference completion, local application, and remote ledger delivery are distinct.
A retained result resumes locally without inference. Ledger delivery is at least once.
"""
from contextlib import nullcontext
from uuid import UUID, uuid4
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging

from .cascade.memory import MemoryEventBackpressure
from .classifier import CASCADE_LABELS, SUPPRESSIVE_LABELS
from .lifecycle import digest, valid_time

log = logging.getLogger(__name__)

MAX_RESULT_BYTES = 16384
TEXT_FIELDS = ('authoritative_backend', 'model_revision', 'prompt_revision',
               'tokenizer_revision', 'taxonomy_revision', 'classifier_id', 'scoring_mode')


def freeze_result(bridge, entry, result):
    value = dict(label=result.label, cache_hit=result.cache_hit,
                 provenance={k: getattr(result, k) for k in TEXT_FIELDS},
                 classifier_identity=bridge._classifier_identity(),
                 completed_at=datetime.now(timezone.utc).isoformat())
    value["provenance"]["authoritative_backend"] = result.authoritative_backend or result.backend
    value['event_id'] = digest(dict(work_id=entry['_work_id'], result=value))
    validate_result(value)
    return value


def validate_result(value):
    required = {'label', 'cache_hit', 'provenance', 'classifier_identity', 'completed_at', 'event_id'}
    if type(value) is not dict or set(value) != required:
        raise ValueError('invalid retained classification result')
    if (value['label'] not in CASCADE_LABELS or type(value['cache_hit']) is not bool
            or not valid_time(value['completed_at'])
            or type(value['provenance']) is not dict or set(value['provenance']) != set(TEXT_FIELDS)
            or any(type(v) is not str for v in value['provenance'].values())):
        raise ValueError('invalid retained classification provenance')
    for name in ('classifier_identity', 'event_id'):
        if (type(value[name]) is not str or len(value[name]) != 64
                or any(c not in '0123456789abcdef' for c in value[name])):
            raise ValueError('invalid retained classification binding')
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_RESULT_BYTES:
        raise ValueError('retained classification result exceeds byte bound')


def fence(bridge, reason):
    bridge._classifier_recovery_error = reason
    bridge._durable_memory_checkpoint_healthy = False
    bridge._durable_memory_checkpoint_failures += 1
    bridge._fence_learning(reason)


def retain(bridge, entry, result):
    """Persist the decision and original targets before publishing any labels."""
    with bridge._lifecycle_lock:
        if bridge._classifier_recovery_error:
            return False
        work_id = entry.setdefault("_work_id", str(uuid4()))
        try:
            value = freeze_result(bridge, entry, result)
            with bridge._pending_memory_lock:
                links = entry.get("_memory_ids", bridge._pending_memory_ids)
                members = []
                for sid in entry.get("_signal_ids") or [entry["signal_id"]]:
                    target = links.get(sid)
                    members.append([sid, str(target) if target else None])
            context = {k: deepcopy(entry[k]) for k in (
                "signal_type", "signal_id", "namespace", "_qualification_bindings", *bridge._context_keys,
            ) if k in entry}
            record = dict(result=value, members=members, context=context)
            record["next_member"] = 0
            bridge._completion_pending[work_id] = record
            with bridge._llm_queue_lock:
                bridge._classifier_inflight[work_id] = entry
            if not bridge._save_state():
                raise OSError("classification result checkpoint failed")
        except Exception as exc:
            fence(bridge, f"classification result retention failed: {exc}")
            return False
        resume(bridge)
        return work_id not in bridge._completion_pending


def spool_identity(bridge):
    if bridge._durable_queue is None:
        return None
    return bridge._durable_queue.identity


def snapshot(bridge):
    journal = {"pending": bridge._completion_pending, "acknowledge": sorted(bridge._completion_ack),
               "work_spool": spool_identity(bridge)}
    return dict(journal, sha256=digest(journal))


def restore(bridge, state):
    """Separate known completions from rc1's interrupted-work replay policy."""
    saved = state.get("classification_completion", {})
    if "classification_completion" in state:
        unsigned = {k: saved[k] for k in ("pending", "acknowledge", "work_spool")}
        if digest(unsigned) != saved.get("sha256"):
            raise ValueError("corrupt classification completion journal")
        if (saved["pending"] or saved["acknowledge"]) and saved["work_spool"] != spool_identity(bridge):
            raise ValueError("classification completion requires its original work spool")
    pending = saved.get("pending", {})
    ack = set(saved.get("acknowledge", []))
    if pending.keys() & ack:
        raise ValueError("conflicting classification completion phases")
    for work_id, record in pending.items():
        value = record["result"]
        validate_result(value)
        unsigned = {k: v for k, v in value.items() if k != "event_id"}
        if (digest(dict(work_id=work_id, result=unsigned)) != value["event_id"]
                or type(record["next_member"]) is not int
                or not 0 <= record["next_member"] < len(record["members"])):
            raise ValueError("retained classification binding mismatch")
        if value["classifier_identity"] != bridge._classifier_identity():
            # The result was retained by a different build or configuration
            # (the identity binds the package implementation). It cannot be
            # trusted by this one, but that is a durable fact, not ambiguity:
            # discard the entry rather than fencing the restore. Tamper
            # evidence (the event_id digest above) stays fatal.
            log.warning(
                "Discarding retained classification %s: bound to a different "
                "classifier identity (build or configuration change)",
                work_id)
            continue
        bridge._classifier_inflight[work_id] = dict(record["context"], _work_id=work_id,
                                                    _signal_ids=[sid for sid, _ in record["members"]])
    bridge._completion_pending = {
        work_id: record for work_id, record in pending.items()
        if record["result"]["classifier_identity"] == bridge._classifier_identity()
    }
    bridge._completion_ack = ack
    owned = pending.keys() | ack
    bridge._durable_staged_entries = [
        e for e in bridge._durable_staged_entries if e["_work_id"] not in owned
    ]
    # Keep retained dispatches accounted and do not manufacture interruption
    # receipts. For ordinary inflight SQLite replay rc1 already subtracts one
    # dispatch; uncount the additional coalesced members here.
    work = state.get("classifier_work")
    if work:
        work["outstanding"] = [e for e in work["outstanding"] if e["work_id"] not in owned]
        recovered = {e["_work_id"]: e for e in bridge._durable_staged_entries}
        for item in work["outstanding"]:
            entry = recovered.get(item["work_id"])
            prior = item.get("signal_ids")
            if entry is None:
                # The lifecycle restore counts one interruption per work item.
                # Account for the other original signals in a coalesced entry.
                if prior:
                    outcome = "dropped" if item["phase"] == "queued" else "failed"
                    work["accounting"][outcome] += len(prior) - 1
                continue
            members = entry.get("_signal_ids") or [entry["signal_id"]]
            if prior is not None:
                if not prior or members[:len(prior)] != prior:
                    raise ValueError("spooled classification members do not match checkpoint")
                work["accounting"]["enqueued"] += len(members) - len(prior)
            if item["phase"] == "inflight":
                work["accounting"]["dispatched"] -= len(prior or members) - 1


def acknowledge(bridge):
    """A committed marker makes both sides of a lost SQLite ack replay-safe."""
    if not bridge._completion_ack:
        return True
    with bridge._llm_queue_lock:
        for work_id in bridge._completion_ack:
            bridge._mark_durable_locked({"_work_id": work_id}, delete=True)
        try:
            if not bridge._flush_durable_locked():
                return False
        except OSError:
            # SQLite may have deleted the row before the caller lost the reply.
            # Keep the checkpointed marker and retry the idempotent delete.
            return False
        bridge._completion_ack.clear()
        return True


def resume(bridge):
    with bridge._lifecycle_lock:
        if bridge._classifier_recovery_error or not acknowledge(bridge):
            return False
        for work_id in list(bridge._completion_pending):
            if not apply(bridge, work_id):
                return False
        return True


def apply(bridge, work_id):
    """Commit the event-reserved prefix, its cursor, and the final ack marker."""
    archive = bridge.memory_archive
    record = bridge._completion_pending[work_id]
    value = record["result"]
    members = record["members"]
    start = cursor = record["next_member"]
    # All-or-nothing destination check. A member whose linked memory was
    # capacity-evicted can never apply, and nothing has been mutated in this
    # pass, so there is no committed decision to protect: keep the work
    # pending and return without fencing (the caller reports
    # classification_completion_pending backpressure). A restart discards the
    # record once the snapshot itself shows the destination gone. Mid-flight
    # eviction inside the application loop still takes the uncertainty fence.
    if archive is not None:
        for sid, target in members[cursor:]:
            if target and archive.get(UUID(target)) is None:
                log.warning(
                    "classification %s member %s target %s evicted before "
                    "application; work stays pending", work_id, sid, target)
                return False
    original = {}
    noise = value["label"] in SUPPRESSIVE_LABELS
    provenance = dict(value["provenance"], classifier_identity=value["classifier_identity"],
                      completed_at=value["completed_at"], cache_hit=value["cache_hit"])
    with bridge._llm_queue_lock, archive._lock if archive is not None else nullcontext():
        if archive is not None:
            old_size, old_bytes = len(archive._events), archive._event_bytes
        finished = False
        try:
            while cursor < len(members):
                sid, target = members[cursor]
                if target:
                    mid = UUID(target)
                    memory = archive.get(mid)
                    if memory is None:
                        raise ValueError("linked memory was evicted or is missing")
                    if mid not in original:
                        original[mid] = (memory, memory.classification, memory.last_modified_at,
                                         deepcopy(memory.evidence))
                    try:
                        archive.update_classification(
                            mid, value["label"], work_id=work_id, signal_id=sid,
                            event_id=digest([value["event_id"], cursor, sid, target]),
                            timestamp=value["completed_at"], provenance=provenance,
                        )
                    except MemoryEventBackpressure:
                        break
                cursor += 1

            if cursor == len(members):
                with bridge._llm_queue_lock:
                    inflight = bridge._classifier_inflight.pop(work_id)
                    bridge._llm_accounting_succeeded += len(members)
                del bridge._completion_pending[work_id]
                if bridge._durable_queue is not None:
                    bridge._completion_ack.add(work_id)
                finished = True
                if not (value["cache_hit"] and noise):
                    # A negative qualification may checkpoint immediately. All
                    # memory, audit, and accounting fields are already staged.
                    if bridge._record_qualification(record["context"], noise) is False:
                        raise OSError("qualification checkpoint failed")
            else:
                record["next_member"] = cursor
                if not noise:
                    bridge._fence_learning("negative classification awaiting audit capacity")
            if not bridge._save_state():
                raise OSError("classification application checkpoint failed")
        except Exception as exc:
            for memory, label, modified, evidence in original.values():
                memory.classification, memory.last_modified_at, memory.evidence = label, modified, evidence
            if archive is not None:
                del archive._events[old_size:]
                archive._event_bytes = old_bytes
            if finished:
                bridge._completion_pending[work_id] = record
                bridge._completion_ack.discard(work_id)
                with bridge._llm_queue_lock:
                    bridge._classifier_inflight[work_id] = inflight
                    bridge._llm_accounting_succeeded -= len(members)
            record["next_member"] = start
            # replace/fsync may have committed before reporting failure.
            # No subsequent snapshot may overwrite that durable decision.
            fence(bridge, f"classification application uncertain: {exc}")
            return False
    with bridge._pending_memory_lock:
        for sid, target in members[start:cursor]:
            if str(bridge._pending_memory_ids.get(sid)) == target:
                bridge._pending_memory_ids.pop(sid, None)
    if finished:
        acknowledge(bridge)
    return finished
