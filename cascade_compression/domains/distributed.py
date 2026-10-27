"""Distributed systems domain pack — HDFS, Hadoop, Spark, Zookeeper logs."""

DOMAIN = "distributed"

SYSTEM_PROMPT = """You are classifying distributed system logs from HDFS (Hadoop Distributed File System), Hadoop MapReduce, Apache Spark, and Zookeeper coordination service. Signal sources include block operations, datanode communication, job scheduling, executor lifecycle, and coordination events.

Context: A distributed cluster processing petabytes generates massive log volume. Most entries are the system doing its job: blocks being written and replicated, datanodes heartbeating, tasks being scheduled and completing, executors being allocated and released. These are the expected operational rhythm.

Problems arise when: blocks become under-replicated, datanodes go offline, tasks fail repeatedly, executors are lost unexpectedly, or Zookeeper sessions expire. These indicate infrastructure issues that need investigation.

The noise/pattern line: successful block operations, routine heartbeats, and task completions are noise or pattern. Under-replicated blocks, failed datanodes, and task retry storms need attention.

Classify as exactly one of:
- routine_noise: successful block writes, routine heartbeats, normal task completions
- known_pattern: block replication in progress, executor allocation, Zookeeper session renewal, periodic checkpoint
- needs_attention: under-replicated blocks, datanode decommission, task retry storm, executor lost unexpectedly
- real_incident: namenode failure, Zookeeper quorum loss, data loss detected, cluster-wide service outage

Answer with one word only."""

FEW_SHOT_EXEMPLARS = {
    "routine_noise": [
        "PacketResponder 0 for block blk_-6952295868487656571 terminating (normal block write completion)",
        "FSNamesystem: BLOCK* allocate blk_12345 at 10.251.73.220:50010 (routine block allocation)",
        "Zookeeper session 0x12a3b4 established (routine client connection)",
    ],
    "known_pattern": [
        "blk_7128370237687728475: Received block of size 67108864 from /10.251.73.220 (normal replication)",
        "Spark: Executor added on worker-1 (routine executor allocation for new job)",
        "Zookeeper: Snapshotting to snapshot.1000000001 (scheduled checkpoint)",
    ],
    "needs_attention": [
        "BLOCK* ask 10.251.73.220 to delete blk_7128370237687728475 (under-replicated, source deleted)",
        "Datanode 10.251.73.220 is unavailable (node lost, blocks at risk)",
        "Spark: Executor lost on worker-2 (unexpected executor failure, task retry)",
        "Zookeeper: Session 0x12a3b4 expired (client lost coordination)",
    ],
    "real_incident": [
        "NameNode: FSNamesystem entering SAFE MODE (namespace unavailable, cluster halted)",
        "Zookeeper: Quorum lost, only 1 of 3 servers responding (coordination service down)",
        "Spark: Job failed — all executors lost on cluster (cluster-level failure)",
    ],
}
