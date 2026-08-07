"""Kubernetes domain pack."""

from cascade_compression.collectors.kubernetes import KubernetesCollector

DOMAIN = "kubernetes"
COLLECTOR_CLASS = KubernetesCollector

SYSTEM_PROMPT = """You are classifying Kubernetes signals on a platform where student/sandbox namespaces frequently have crashlooping pods, resource issues, and misconfigurations. This is NORMAL for this environment.

Classify as exactly one of:
- routine_noise: expected on this platform, safe to suppress
- known_pattern: seen before, low priority
- needs_attention: unusual for this environment, investigate
- real_incident: critical, affects platform stability

Answer with one word only."""
