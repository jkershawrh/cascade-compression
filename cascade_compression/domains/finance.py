"""Financial services domain pack."""

DOMAIN = "finance"
COLLECTOR_CLASS = None  # Set to FinanceCollector when implemented

SYSTEM_PROMPT = """You are classifying financial transaction signals. Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""
