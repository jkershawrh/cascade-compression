"""Ansible Automation Platform domain pack."""

from cascade_compression.collectors.aap import AAPCollector

DOMAIN = "aap"
COLLECTOR_CLASS = AAPCollector

SYSTEM_PROMPT = """You are classifying Ansible Automation Platform (AAP) signals from three sources: playbook task events, job outcomes, and configuration changes.

Context: Infrastructure automation platform running scheduled operations, sandbox provisioning, and monitoring. Most tasks succeed routinely.

Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""
