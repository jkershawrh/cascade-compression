# Advisory exact-repeat triage

`CASCADE_NANO_PROFILE=triage_only` is an opt-in evaluation mode that preserves
every input signal while adding an advisory group to fully identified, exact
repeated Kubernetes Pod Events. It bypasses nano-agent removal, keeps original
severity and content, and reports zero compression. This is useful for human
triage experiments; it is not an actionability verdict or an inference-cost
optimization.

An eligible Event must be medium severity and include cluster, namespace, Pod
name and UID, Kubernetes Event UID, reason, integer Event count, and a
64-character SHA-256 digest of the full message. Timestamp-only churn does not
split a group. Compliance, fraud, sanctions, high-severity, incomplete, and
other-domain signals receive no tag. Group state is bounded to 50,000 entries,
expires after 60 seconds, and is intentionally lost on restart.

The default `legacy` profile is unchanged. A separate
`verified_repeat_only` profile may remove only qualified exact repeats while
preserving the first observation and all unqualified signals. Both profiles
are opt-in. Validate their workload-specific safety and downstream cost before
production use.

For bounded evaluations, `/stats` exposes `llm_signal_outcomes_since_start`.
Its top-level enqueued, dispatched, classified, failed, and dropped counts are
restored from the classifier-work checkpoint and therefore span restarts:
`accounting_since` names the instant they count from
(`accounting_origin_known: false` for a checkpoint written before that field
existed), while `started_at` names only the current process. Read the nested
`process` block for this process's own flow, and `unattributed` for the signed
gap between lifetime outcomes and lifetime admissions. `queued` and `in_flight`
are live gauges, and `reconciled` / `terminal_complete` apply to the restored
total. Do not combine counters across restarts without recording the epoch.
