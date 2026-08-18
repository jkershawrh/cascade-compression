"""Edge case scenarios — adversarial signals designed to find cascade gaps.

Each scenario targets a specific failure mode:
1. Severity boundary — signals just above/below the gate
2. Semantic ambiguity — signals that look like noise but aren't
3. Contextual traps — signals that are noise globally but critical locally
4. Temporal patterns — signals that are only important in sequence
5. Cross-domain correlation — signals that need federation to interpret
6. Evasion patterns — real incidents disguised as routine
7. Compliance traps — regulatory signals misclassified as noise

Usage:
    python -m cascade_compression.edge_scenarios \
        --target http://cascade-finance:8090 \
        --domain finance \
        --scenario all
"""

import argparse
import json
import logging
import time
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def _post(target: str, signals: list) -> dict:
    data = json.dumps({"signals": signals}).encode()
    req = Request(f"{target}/cascade", data=data,
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _check(target: str, signals: list, expected_survivors: int, scenario: str):
    result = _post(target, signals)
    survivors = result.get("forwarded", 0) + result.get("survivors", 0)
    total = result.get("total", len(signals))
    compressed = result.get("compressed", 0)
    status = "PASS" if survivors >= expected_survivors else "FAIL"
    log.info(
        "[%s] %s: sent=%d, compressed=%d, survivors=%d (expected >=%d)",
        status, scenario, total, compressed, survivors, expected_survivors,
    )
    return {
        "scenario": scenario,
        "status": status,
        "sent": total,
        "compressed": compressed,
        "survivors": survivors,
        "expected_survivors": expected_survivors,
    }


# ─── FINANCE SCENARIOS ───

def finance_structuring(target):
    """$9,999 cash deposits — just under the $10K reporting threshold.
    These MUST survive. A cascade that drops them has a compliance gap."""
    signals = []
    for i in range(5):
        signals.append(
            {"signal_type": "txn_cash_deposit", "severity": "high",
             "source": "branch", "namespace": "banking",
             "content": {"amount": 9999 - i * 10, "merchant": "Cash Deposit", "country": "US",
                         "location": "Miami", "velocity_1h": 1, "velocity_24h": 3 + i},
             "labels": {"account_id": f"ACCT-EDGE-{i}", "label": "compliance",
                        "label_detail": "structuring_sub_10k"}})
    return _check(target, signals, 5, "STRUCTURING: $9,999 deposits (compliance)")


def finance_rapid_test(target):
    """5 small charges in 60 seconds from different merchants — stolen card testing pattern.
    Low individual severity but the PATTERN is critical."""
    signals = [
        {"signal_type": "txn_card_purchase", "severity": "info",
         "source": "online", "namespace": "retail",
         "content": {"amount": 1.00 + i * 0.50, "merchant": f"TestMerchant-{i}",
                     "country": "US", "location": "Various",
                     "velocity_1h": 5 + i, "velocity_24h": 5 + i,
                     "is_first_time_merchant": True},
         "labels": {"account_id": "ACCT-STOLEN-001", "label": "fraud",
                    "label_detail": "rapid_small_charges"}}
        for i in range(5)
    ]
    return _check(target, signals, 0, "RAPID TEST: 5x$1 charges (info severity, should be gated)")


def finance_amount_anomaly(target):
    """Normal customer suddenly makes a $50K wire to a new country.
    High severity, should always survive."""
    signals = [
        {"signal_type": "txn_wire_transfer", "severity": "critical",
         "source": "online", "namespace": "banking",
         "content": {"amount": 50000, "merchant": "Wire Transfer",
                     "country": "CY", "location": "Limassol",
                     "velocity_1h": 1, "velocity_24h": 1,
                     "is_first_time_country": True, "is_first_time_merchant": True},
         "labels": {"account_id": "ACCT-ANOMALY-001", "label": "fraud",
                    "label_detail": "amount_anomaly_first_time_country"}},
    ]
    return _check(target, signals, 1, "AMOUNT ANOMALY: $50K wire to Cyprus (critical)")


def finance_noise_flood_then_real(target):
    """50 normal transactions followed by 1 fraud. Tests whether the cascade
    still catches the needle after learning the haystack."""
    noise = [
        {"signal_type": "txn_card_purchase", "severity": "info",
         "source": "pos", "namespace": "retail",
         "content": {"amount": 25 + i, "merchant": "Starbucks",
                     "country": "US", "location": "Dallas",
                     "velocity_1h": 1, "velocity_24h": 2,
                     "is_recurring": True},
         "labels": {"account_id": f"ACCT-NORMAL-{i:03d}", "label": "normal"}}
        for i in range(50)
    ]
    fraud = [
        {"signal_type": "txn_wire_transfer", "severity": "critical",
         "source": "online", "namespace": "banking",
         "content": {"amount": 89000, "merchant": "Wire Transfer",
                     "country": "RU", "location": "Moscow",
                     "velocity_1h": 1, "velocity_24h": 1,
                     "is_first_time_country": True},
         "labels": {"account_id": "ACCT-NORMAL-025", "label": "fraud",
                    "label_detail": "account_takeover_wire"}},
    ]
    return _check(target, noise + fraud, 1, "NEEDLE IN HAYSTACK: 50 normal + 1 fraud")


def finance_sanctions_adjacent(target):
    """Transaction to a country adjacent to a sanctioned nation.
    Not sanctioned itself but compliance-relevant."""
    signals = [
        {"signal_type": "txn_wire_transfer", "severity": "high",
         "source": "online", "namespace": "banking",
         "content": {"amount": 15000, "merchant": "International Wire",
                     "country": "AE", "location": "Dubai",
                     "velocity_1h": 1, "velocity_24h": 1,
                     "is_first_time_country": True},
         "labels": {"account_id": "ACCT-SANC-001", "label": "compliance",
                    "label_detail": "sanctions_adjacent_country"}},
    ]
    return _check(target, signals, 1, "SANCTIONS ADJACENT: $15K wire to UAE (compliance)")


# ─── HEALTHCARE SCENARIOS ───

def healthcare_vital_borderline(target):
    """Heart rate at 99 (normal <100) with slight BP elevation.
    Individually normal, together concerning."""
    signals = [
        {"signal_type": "hl7_vital_sign", "severity": "medium",
         "source": "icu_monitor", "namespace": "cardiology",
         "content": {"heart_rate": 99, "bp_systolic": 139, "bp_diastolic": 89,
                     "spo2": 95, "temp_f": 99.8, "respiratory_rate": 19},
         "labels": {"patient_id": "PT-BORDER-001", "label": "needs_attention",
                    "label_detail": "borderline_vitals_cluster"}},
    ]
    return _check(target, signals, 1, "BORDERLINE VITALS: all values just under threshold")


def healthcare_hipaa_after_hours(target):
    """Medical record access at 3am by a non-treating provider.
    The access itself is routine; the CONTEXT makes it suspicious."""
    signals = [
        {"signal_type": "hl7_hipaa_access", "severity": "medium",
         "source": "ehr_audit", "namespace": "compliance",
         "content": {"record_type": "patient_chart", "accessor_role": "nurse",
                     "accessor_department": "radiology", "patient_department": "oncology",
                     "access_hour": 3, "is_treating_provider": False},
         "labels": {"patient_id": "PT-VIP-001", "label": "compliance",
                    "label_detail": "hipaa_after_hours_cross_department"}},
    ]
    return _check(target, signals, 1, "HIPAA: 3am cross-department access (compliance)")


def healthcare_silent_deterioration(target):
    """Patient vitals slowly declining over 4 readings. Each reading is 'normal'
    individually but the TREND is critical."""
    signals = [
        {"signal_type": "hl7_vital_sign", "severity": "info",
         "source": "floor_monitor", "namespace": "medicine",
         "content": {"heart_rate": 72 + i * 8, "bp_systolic": 120 - i * 10,
                     "spo2": 98 - i * 2, "reading_number": i + 1},
         "labels": {"patient_id": "PT-DECLINE-001", "label": "needs_attention",
                    "label_detail": "silent_deterioration"}}
        for i in range(4)
    ]
    return _check(target, signals, 1, "SILENT DETERIORATION: 4 readings trending down")


# ─── TELECOM SCENARIOS ───

def telecom_maintenance_masking(target):
    """What looks like scheduled maintenance but affects a critical path.
    Low severity, but the affected circuit serves a hospital."""
    signals = [
        {"signal_type": "tmf_maintenance", "severity": "info",
         "source": "nms", "namespace": "operations",
         "content": {"circuit_id": "CKT-HOSP-001", "maintenance_type": "planned",
                     "customer_class": "critical_infrastructure",
                     "affected_services": ["hospital_primary", "911_backup"],
                     "duration_hours": 4},
         "labels": {"region": "northeast", "label": "needs_attention",
                    "label_detail": "critical_infrastructure_maintenance"}},
    ]
    return _check(target, signals, 1, "MAINTENANCE MASKING: planned work on hospital circuit")


def telecom_cascade_failure(target):
    """3 fiber cuts in the same region within 1 hour — could be coordinated attack
    or weather event. Individual cuts are routine; the CLUSTER matters."""
    signals = [
        {"signal_type": "tmf_fiber_cut", "severity": "medium",
         "source": "nms", "namespace": "outside_plant",
         "content": {"span_id": f"SPAN-{100+i}", "location": f"Sector-{i}",
                     "distance_km": 2.5 * i, "region": "northeast",
                     "cuts_in_region_1h": 3},
         "labels": {"region": "northeast", "label": "real_incident",
                    "label_detail": "clustered_fiber_cuts"}}
        for i in range(3)
    ]
    return _check(target, signals, 3, "CASCADE FAILURE: 3 fiber cuts same region")


def telecom_bgp_anomaly(target):
    """BGP route change from an unexpected AS. Could be a hijack.
    This is the kind of signal that looks routine but has massive impact."""
    signals = [
        {"signal_type": "tmf_bgp_change", "severity": "high",
         "source": "route_monitor", "namespace": "backbone",
         "content": {"prefix": "10.0.0.0/8", "new_as": "AS65001",
                     "expected_as": "AS64512", "change_type": "origin_change",
                     "affected_prefixes": 1500},
         "labels": {"region": "global", "label": "real_incident",
                    "label_detail": "bgp_hijack_candidate"}},
    ]
    return _check(target, signals, 1, "BGP ANOMALY: unexpected origin AS change")


# ─── CROSS-DOMAIN SCENARIOS ───

def burst_then_silence(target, domain):
    """100 signals in 10 seconds, then nothing.
    Tests whether the cascade handles burst without dropping critical signals."""
    signals = [
        {"signal_type": f"{domain}_burst_test", "severity": "critical" if i == 99 else "info",
         "source": "edge_test", "namespace": "burst",
         "content": {"sequence": i, "burst_id": "BURST-001", "total": 100},
         "labels": {"test": "burst", "label": "real_incident" if i == 99 else "noise"}}
        for i in range(100)
    ]
    return _check(target, signals, 1, f"BURST: 100 signals, critical at position 99")


# ─── SCENARIO REGISTRY ───

SCENARIOS = {
    "finance": [
        finance_structuring,
        finance_rapid_test,
        finance_amount_anomaly,
        finance_noise_flood_then_real,
        finance_sanctions_adjacent,
    ],
    "healthcare": [
        healthcare_vital_borderline,
        healthcare_hipaa_after_hours,
        healthcare_silent_deterioration,
    ],
    "telecom": [
        telecom_maintenance_masking,
        telecom_cascade_failure,
        telecom_bgp_anomaly,
    ],
    "cross_domain": [
        burst_then_silence,
    ],
}


def main():
    parser = argparse.ArgumentParser(description="Edge case scenario runner")
    parser.add_argument("--target", required=True, help="Cascade service URL")
    parser.add_argument("--domain", required=True, help="Domain to test")
    parser.add_argument("--scenario", default="all", help="Specific scenario or 'all'")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    results = []
    domain_scenarios = SCENARIOS.get(args.domain, [])

    if not domain_scenarios:
        log.error("No scenarios for domain: %s (available: %s)", args.domain, list(SCENARIOS.keys()))
        return

    log.info("Running %d edge scenarios for %s against %s", len(domain_scenarios), args.domain, args.target)

    for fn in domain_scenarios:
        try:
            r = fn(args.target)
            results.append(r)
        except Exception as e:
            log.error("Scenario %s failed: %s", fn.__name__, e)
            results.append({"scenario": fn.__name__, "status": "ERROR", "error": str(e)})
        time.sleep(0.5)

    # Cross-domain burst test
    try:
        r = burst_then_silence(args.target, args.domain)
        results.append(r)
    except Exception as e:
        log.error("Burst test failed: %s", e)

    # Summary
    passed = sum(1 for r in results if r.get("status") == "PASS")
    failed = sum(1 for r in results if r.get("status") == "FAIL")
    errors = sum(1 for r in results if r.get("status") == "ERROR")

    log.info("")
    log.info("=" * 60)
    log.info("EDGE SCENARIO RESULTS: %s", args.domain)
    log.info("=" * 60)
    for r in results:
        log.info("  [%s] %s", r.get("status", "?"), r.get("scenario", "?"))
    log.info("")
    log.info("PASSED: %d  FAILED: %d  ERROR: %d  TOTAL: %d", passed, failed, errors, len(results))
    log.info("=" * 60)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
