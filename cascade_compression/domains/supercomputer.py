"""Supercomputer domain pack — BGL, HPC, Thunderbird log analysis."""

DOMAIN = "supercomputer"

SYSTEM_PROMPT = """You are classifying supercomputer system logs from high-performance computing clusters (IBM Blue Gene/L and similar). Signal sources include RAS (Reliability, Availability, Serviceability) events, hardware sensor reports, kernel messages, and application-level events.

Context: A supercomputer with 131,072 compute nodes generates millions of log entries per day. The overwhelming majority are routine: instruction cache parity errors that self-correct, packet responder terminations after successful block operations, and scheduled hardware monitoring events. These are the expected operating rhythm of a massively parallel system.

Critical events — node hardware failures, thermal shutdowns, memory uncorrectable errors, and system-wide outages — are rare but require immediate operator response.

The noise/pattern line: routine RAS events, self-correcting hardware errors, and expected block operations are noise or pattern. Repeated hardware errors on the same node, escalating error rates, and infrastructure-level failures need attention.

Classify as exactly one of:
- routine_noise: self-correcting hardware events, routine monitoring, expected block operations
- known_pattern: recurring hardware corrections on a stable node, expected kernel messages, scheduled maintenance events
- needs_attention: repeated hardware errors on one node, escalating error rates, degraded links, thermal warnings
- real_incident: node hardware failure requiring replacement, uncorrectable memory errors, system-wide outage, thermal shutdown

Answer with one word only."""

FEW_SHOT_EXEMPLARS = {
    "routine_noise": [
        "instruction cache parity error corrected (routine self-correcting hardware event)",
        "PacketResponder 1 for block blk_38865049064139660 terminating (normal block operation completion)",
        "BLOCK* NameSystem.addStoredBlock: blockMap updated (routine block registration)",
    ],
    "known_pattern": [
        "RAS KERNEL INFO: instruction cache parity error corrected on R02-M1-N0-C:J12 (recurring on this node, self-correcting)",
        "generating core file in /tmp (routine application crash dump during debugging)",
        "MMCS: fuel gauge sensor normal reading (scheduled hardware monitoring)",
    ],
    "needs_attention": [
        "RAS KERNEL WARNING: L2 cache error rate increasing on R01-M1-N4-C:J18 (degrading hardware)",
        "data VLAN partition 0 is not available (network partition affecting job communication)",
        "RAS HARDWARE WARNING: temperature 82C approaching thermal limit on R04-M0-N2 (cooling issue)",
    ],
    "real_incident": [
        "RAS HARDWARE CRITICAL: node R02-M1-N0-C:J12 CPU failed, node marked for replacement",
        "MMCS CRITICAL: uncorrectable memory error in compute node, job terminated",
        "RAS KERNEL CRITICAL: system dump initiated, all compute halted (system-wide failure)",
    ],
}
