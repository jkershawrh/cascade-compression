"""Local lifecycle controls; digests bind policy bytes, not evidence authenticity."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone

REVISION = 'cascade-lifecycle-evidence-v5'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def valid_time(value, ttl=None):
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - instant).total_seconds()
        return age >= 0 and (ttl is None or ttl > 0 and age < ttl * 3600)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return False


def atomic_snapshot(path, state):
    """Replace only after a complete owner-only write and fsync."""
    parent = os.path.dirname(os.path.abspath(path))
    fd, temporary = tempfile.mkstemp(prefix='.cascade-state-', dir=parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LifecycleControls:
    # Context keys for multi-key contextual decomposition. Set by the bridge
    # from CASCADE_CONTEXT_KEYS. Each key produces its own context identity
    # during classification recording and its own discovery path.
    _context_keys = ('namespace',)

    def _classifier_identity(self):
        if not hasattr(self, '_lifecycle_implementation_sha256'):
            root = Path(__file__).resolve().parent
            self._lifecycle_implementation_sha256 = digest({
                str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(root.rglob('*.py'))})
        def backend(value):
            names = ('name', 'micro_model', 'macro_model', 'system_prompt', 'url',
                     'address', 'signal_name', 'deadline_seconds', 'identity_contract', 'prompt_revision',
                     'expected_taxonomy_revision', 'expected_model_revision',
                     'expected_tokenizer_revision', 'expected_classifier_id', 'scoring_mode')
            return {key: getattr(value, key, None) for key in names}
        return digest(dict(revision=REVISION, serializer=self._serializer_revision,
                           implementation=self._lifecycle_implementation_sha256,
                           profile=self.nano_profile, domain=self.domain,
                           human_gate=self._human_gate, context_key=self._contextual_suppressor._context_key,
                           ttl=self._activation_ttl_hours, mode=self.classifier.mode,
                           qualification_ttl=self._qualification_ttl_hours,
                           approval_ttl=self.promotion.approval_ttl_hours,
                           margin=self.classifier.hybrid_margin,
                           suppress_margin=self.classifier.hybrid_suppress_margin,
                           generative=backend(self.classifier.generative),
                           semantic=backend(self.classifier.semantic)))

    def _rule_binding(self, metrics, rule):
        return digest(dict(classifier=self._classifier_identity(), config=metrics.config,
                           name=rule.name, condition=rule.condition, signal_types=rule.signal_types,
                           classification=rule.classification))

    def _context_binding(self, identity):
        return digest(dict(classifier=self._classifier_identity(), context=identity))

    def _qualified_record(self, key, binding):
        record = self._qualification_evidence.get(key, {})
        if (record.get('binding') != binding or record.get('revision') != REVISION
                or type(record.get('noise')) is not int or record['noise'] < 0
                or type(record.get('important')) is not int or record['important'] < 0):
            return None
        first, last = record.get('first_observed_at'), record.get('last_observed_at')
        if not valid_time(first, self._qualification_ttl_hours) or not valid_time(last, self._qualification_ttl_hours):
            return None
        if datetime.fromisoformat(first.replace('Z','+00:00')) > datetime.fromisoformat(last.replace('Z','+00:00')):
            return None
        return record

    def _fence_learning(self, reason):
        """Withdraw only adaptive authority; deterministic bounded policy remains."""
        self._state_healthy = False
        for kind in list(self._activated_types):
            self._deactivate_agent(kind)
        for kind, contexts in self._activated_contexts.items():
            for context in list(contexts):
                self._contextual_suppressor.remove_noise_context(kind, context)
            contexts.clear()
        self._context_activation_timestamps.clear()
        for metrics in self._agent_metrics.values():
            if metrics.tier in {'nano','micro','macro','pending_approval'}:
                self.promotion.demote(metrics, reason=reason)
        self._qualification_evidence.clear()
        self._llm_noise_counts.clear(); self._llm_important_counts.clear()
        self._llm_context_noise.clear(); self._llm_context_important.clear()

    def _qualification_bindings(self, signal):
        kind = signal['signal_type']
        keys = {}
        for name, metrics in list(self._agent_metrics.items()):
            rule = self._agent_rules.get(name)
            if (rule and metrics.config.get('signal_type') == kind
                    and metrics.config.get('pattern_type') in {'dominant_type','repeat_flood'}):
                keys['agent:' + name] = self._rule_binding(metrics, rule)
        context = f"{kind}:{signal.get('namespace', '')}"
        keys['context:' + context] = self._context_binding(context)
        return keys

    def _record_qualification(self, signal, is_noise):
        """Only actual successful classification results enter qualification counts.

        New/changed rules start at zero. Historical type aggregates remain useful
        telemetry but cannot confer authority on a new rule or configuration.
        """
        with self._lifecycle_lock:
            if self._state_file and not self._state_healthy:
                self._fence_learning('unhealthy qualification checkpoint')
                return False
            kind = signal['signal_type']
            # Record context counts for every configured context key.
            # Each key produces its own identity: e.g., namespace produces
            # "task_verbose:aap-general" and source produces
            # "task_verbose:source:DeployCluster". Discovery promotes pure
            # contexts per-key; each key's suppressor handles its own.
            context_identities = []
            for ctx_key in self._context_keys:
                ctx_value = signal.get(ctx_key, '')
                if ctx_value:
                    context_identities.append(f"{kind}:{ctx_value}")
            if is_noise:
                self.stats.llm_noise += 1
                self._llm_noise_counts[kind] += 1
                self._llm_important_counts.setdefault(kind, 0)
                for identity in context_identities:
                    self._increment_context_count(self._llm_context_noise, identity)
            else:
                self.stats.llm_important += 1
                self._llm_important_counts[kind] += 1
                for identity in context_identities:
                    self._increment_context_count(self._llm_context_important, identity)
            keys = self._qualification_bindings(signal)
            started = signal.get('_qualification_bindings', {})
            for key, binding in keys.items():
                if started.get(key) != binding:
                    continue
                record = self._qualified_record(key, binding)
                if record is None:
                    if key.startswith('context:'):
                        contexts = [entry for entry in self._qualification_evidence if entry.startswith('context:')]
                        if len(contexts) >= self._CONTEXT_MAP_MAX:
                            self._qualification_evidence.pop(contexts[0], None)
                    record = dict(binding=binding, revision=REVISION, noise=0, important=0,
                                  first_observed_at=datetime.now(timezone.utc).isoformat())
                    self._qualification_evidence[key] = record
                record['noise' if is_noise else 'important'] += 1
                record['last_observed_at'] = datetime.now(timezone.utc).isoformat()
            if not is_noise and (kind in self._activated_types or self._activated_contexts.get(kind)):
                # A negative validation result immediately withdraws authority;
                # it need not wait for an asynchronous promotion interval.
                self._deactivate_agent(kind)
                for name, metrics in self._agent_metrics.items():
                    if metrics.config.get('signal_type') == kind and metrics.tier in {'nano','micro','macro'}:
                        metrics.last_batch_fn_count = 1
                        self.promotion.demote(metrics, reason='negative classifier validation; suppression authority withdrawn')
                for ctx in list(self._activated_contexts.get(kind, set())):
                    self._contextual_suppressor.remove_noise_context(kind, ctx)
                    self._context_activation_timestamps.pop(f'{kind}:{ctx}', None)
                self._activated_contexts[kind].clear()
                self._flush_promotion_events()
            if not is_noise and self._state_file and not self._save_state():
                self._fence_learning('negative validation could not be durably checkpointed')
                return False
            return True

    def _restored_authority_valid(self, kind, pattern):
        if pattern not in {'dominant_type', 'repeat_flood'}:
            return False
        noise = self._llm_noise_counts.get(kind)
        important = self._llm_important_counts.get(kind)
        if type(noise) is not int or noise < 200 or type(important) is not int or important != 0:
            return False
        if not valid_time(self._activation_timestamps.get(kind), self._activation_ttl_hours):
            return False
        for name, metrics in self._agent_metrics.items():
            rule = self._agent_rules.get(name)
            if (not rule or metrics.config.get('signal_type') != kind
                    or metrics.config.get('pattern_type') != pattern
                    or metrics.tier != 'nano' or metrics.deactivated):
                continue
            record = self._qualified_record('agent:' + name, self._rule_binding(metrics, rule))
            if not record or record['noise'] < 200 or record['important'] != 0:
                continue
            if self._human_gate and not (
                metrics.human_approved is True and isinstance(metrics.human_approved_by, str)
                and metrics.human_approved_by.strip() and valid_time(metrics.human_approved_at, self.promotion.approval_ttl_hours)
            ):
                continue
            return True
        return False

    def _validate_restored_tiers(self):
        """Every saved activated-tier entry must have actual restored authority.

        An orphan tier is not a pending candidate and cannot reuse its old
        qualification record to mint a new activation timestamp on discovery.
        """
        invalid_types = set()
        for name, metrics in self._agent_metrics.items():
            if metrics.tier not in {'nano','micro','macro'}:
                continue
            kind, pattern = metrics.config.get('signal_type'), metrics.config.get('pattern_type')
            if pattern in {'dominant_type','repeat_flood'}:
                rule = self._agent_rules.get(name)
                record = self._qualified_record('agent:'+name,self._rule_binding(metrics,rule)) if rule else None
                valid = (metrics.tier=='nano' and not metrics.deactivated and record
                         and record['noise']>=200 and record['important']==0
                         and self._activated_patterns.get(kind)==pattern and self._restored_authority_valid(kind,pattern))
                if not valid:
                    invalid_types.add(kind)
            elif pattern == 'contextual_noise':
                context = metrics.config.get('context_value')
                identity = f'{kind}:{context}'
                if metrics.tier!='nano' or metrics.deactivated or context not in self._activated_contexts.get(kind,set()):
                    self._qualification_evidence.pop('context:'+identity,None)
                    self._activated_contexts[kind].discard(context)
                    self._contextual_suppressor.remove_noise_context(kind,context)
                    self._context_activation_timestamps.pop(identity,None)
                    metrics.tier = 'draft'
            else:
                metrics.tier = 'draft'
                self._qualification_evidence.pop('agent:'+name,None)
        for kind in invalid_types:
            self._deactivate_agent(kind)
            self._llm_noise_counts[kind] = self._llm_important_counts[kind] = 0
            for name, metrics in self._agent_metrics.items():
                if metrics.config.get('signal_type')==kind and metrics.config.get('pattern_type') in {'dominant_type','repeat_flood'}:
                    self._qualification_evidence.pop('agent:'+name,None)
                    metrics.tier = 'draft'
                    metrics.human_approved = False
                    metrics.human_approved_by = metrics.human_approved_at = None

    def _classifier_work_snapshot(self):
        with self._llm_queue_lock:
            return dict(
                accounting={name: getattr(self, '_llm_accounting_' + name)
                            for name in ('enqueued', 'dispatched', 'succeeded', 'failed', 'dropped')},
                outstanding=[dict(work_id=e['_work_id'], signal_id=e.get('signal_id'),
                                  signal_type=e.get('signal_type'), phase=phase)
                             for phase, entries in [('queued', self._llm_buffer + self._llm_priority_buffer),
                                                    ('inflight', list(self._classifier_inflight.values()))]
                             for e in entries if '_work_id' in e],
                terminal_receipts=list(self._classifier_terminal_receipts),
                terminal_receipts_evicted=self._classifier_terminal_evicted)

    def _restore_classifier_work(self, work):
        """Interrupted work is explicitly terminal, never falsely marked successful.

        Receipts name each work/observation ID and its previous phase. This is
        terminal accounting rather than automatic replay or exactly-once work.
        """
        if not work:
            return
        with self._llm_queue_lock:
            for name in ('enqueued', 'dispatched', 'succeeded', 'failed', 'dropped'):
                value = work.get('accounting', {}).get(name, 0)
                if type(value) is not int or value < 0:
                    raise ValueError('invalid classifier accounting snapshot')
                setattr(self, '_llm_accounting_' + name, value)
            self._classifier_terminal_receipts = list(work.get('terminal_receipts', []))
            self._classifier_terminal_evicted = work.get('terminal_receipts_evicted', 0)
            seen = {receipt['work_id'] for receipt in self._classifier_terminal_receipts}
            for entry in work.get('outstanding', []):
                identifier = entry['work_id']
                if identifier not in seen:
                    outcome = 'dropped' if entry.get('phase') == 'queued' else 'failed'
                    self._classifier_terminal_receipts.append(dict(
                        **entry, outcome=outcome, reason='restart_interrupted',
                        prior_execution='unknown' if entry.get('phase') == 'inflight' else 'not_dispatched',
                        timestamp=datetime.now(timezone.utc).isoformat()))
                    name = '_llm_accounting_' + outcome
                    setattr(self, name, getattr(self, name) + 1)
                    seen.add(identifier)
            excess = max(0, len(self._classifier_terminal_receipts) - self._classifier_terminal_max)
            if excess:
                self._classifier_terminal_evicted += excess
                self._classifier_terminal_receipts = self._classifier_terminal_receipts[excess:]
