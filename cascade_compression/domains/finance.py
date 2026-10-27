"""Financial services domain pack."""

from cascade_compression.collectors.finance import FinanceCollector

DOMAIN = "finance"
COLLECTOR_CLASS = FinanceCollector

SYSTEM_PROMPT = """You are classifying financial transaction signals from a card payment processing system. Signal sources include transaction authorizations, account activity, fraud detection alerts, and regulatory flags.

Context: A payment processing system handles millions of normal transactions per day. The overwhelming majority are routine (expected merchant, normal amount, typical pattern). Fraud is rare (typically <1% of volume) but critical to catch.

The noise/pattern line: normal transactions that match the cardholder's pattern are noise or pattern; unusual amounts, unfamiliar merchants, or rapid-fire transactions need attention. Confirmed fraud is an incident.

Classify as exactly one of:
- routine_noise: transaction matching the cardholder's normal pattern, no anomalies
- known_pattern: recurring transaction (subscription, monthly bill, expected merchant)
- needs_attention: unusual amount, new merchant, geographic anomaly, velocity spike
- real_incident: confirmed fraud, account takeover, regulatory violation

Answer with one word only."""

FEW_SHOT_EXEMPLARS = {
    "routine_noise": [
        "Transaction amount=$12.50 coffee_shop_123 (matches daily spending pattern)",
        "Transaction amount=$45.00 grocery_store (weekly grocery, normal amount)",
        "Transaction amount=$2.99 streaming_service (monthly subscription, recurring)",
    ],
    "known_pattern": [
        "Transaction amount=$89.00 gym_membership (monthly recurring charge)",
        "Transaction amount=$1200.00 rent_payment (monthly, expected large transaction)",
        "Transaction amount=$15.99 cloud_storage (annual subscription renewal)",
    ],
    "needs_attention": [
        "Transaction amount=$4800 electronics_store (4x normal single-transaction amount)",
        "Transaction in foreign_country at 3am (card not present, unusual geography and time)",
        "Five transactions in 10 minutes (velocity spike, possible card testing)",
        "Transaction amount=$2500 new_merchant (first interaction, high amount)",
    ],
    "real_incident": [
        "Confirmed fraudulent transaction (chargeback filed, card compromised)",
        "Account takeover detected (password change + immediate high-value transactions)",
        "Money laundering pattern detected (structured transactions below reporting threshold)",
    ],
}
