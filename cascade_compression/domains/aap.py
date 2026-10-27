"""AAP (Ansible Automation Platform) domain pack.

Signal sources: playbook task events (runner_on_ok, runner_on_skipped,
runner_on_failed, item_on_skipped), verbose task output, job lifecycle
events, configuration changes.

Context: AAP runs automation playbooks that build and operate
environments. A single playbook run produces hundreds of task events.
The overwhelming majority are routine (steps succeeding, skipping
under when-conditions, verbose logging). Failures and warnings are
the signals that need attention — but only when they're unexpected
(a destroy playbook expects some failures during cleanup).
"""

DOMAIN = "aap"

SYSTEM_PROMPT = """You are classifying automation task signals from an Ansible Automation Platform. Signal sources include playbook task results (ok, changed, skipped, failed), verbose task output, job lifecycle events, and configuration changes.

Context: AAP runs automation playbooks that build, operate, and destroy environments. A single playbook produces hundreds of task events — most succeed, some skip under when-conditions, some log verbose detail. These are expected operational noise. Failed tasks and warnings are attention-worthy when they're unexpected (a deploy playbook failing is a problem; a destroy playbook skipping already-absent resources is normal).

The noise/pattern line: successful and skipped tasks are the playbook working as designed (noise or pattern). Failed tasks and warnings need investigation unless they're part of an expected cleanup/destroy flow.

Classify as exactly one of:
- routine_noise: content-free repetition (successful task results that carry no new information)
- known_pattern: expected automation event (task skipped by condition, verbose output, job started/completed, config updated)
- needs_attention: unexpected task failure, credential problems, configuration errors, jobs stuck or canceled
- real_incident: automation platform failure, playbook corruption, credential exposure, widespread job failures

Answer with one word only."""

FEW_SHOT_EXEMPLARS = {
    "routine_noise": [
        "Task completed successfully: Create dynamic_cache directories (expected step in every run)",
        "Task completed successfully: copy the environment .pem key (routine credential setup)",
        "Job started: provisioning environment (normal lifecycle event)",
    ],
    "known_pattern": [
        "Task skipped: Save Route53User credentials (when-condition not met for this stack type)",
        "Task skipped: Invoke roles to remove ocp workloads (nothing to remove in this environment)",
        "Verbose: Deploying Pre Infra workloads on localhost (expected detail logging)",
        "Task skipped: Filter the created clusters (no clusters match the filter criteria)",
    ],
    "needs_attention": [
        "Task failed: Fail if ocp_console_embed_domain is not set (required variable missing)",
        "Credential update failed: unable to authenticate to the registry (credential may be expired)",
        "Job canceled: environment provisioning exceeded timeout (possible infrastructure issue)",
        "Task failed: Unable to connect to the API server (connectivity or cert problem)",
    ],
    "real_incident": [
        "Automation controller unresponsive: all jobs queued but none dispatching (platform failure)",
        "Credential exposed in task output: private key visible in verbose logging (security event)",
    ],
}

MEMORY_CONFIG = {
    "decay": {
        "routine_noise": {"half_life_hours": 12, "min_strength": 0.05},
        "known_pattern": {"half_life_hours": 48, "min_strength": 0.05},
        "needs_attention": {"half_life_hours": 168, "min_strength": 0.1},
        "real_incident": {"half_life_hours": 720, "min_strength": 0.2},
    },
    "consolidation_interval_hours": 4,
    "eviction_threshold": 0.05,
}
