"""Healthcare domain pack."""

from cascade_compression.collectors.healthcare import HealthcareCollector

DOMAIN = "healthcare"
COLLECTOR_CLASS = HealthcareCollector

SYSTEM_PROMPT = """You are classifying clinical event signals from a hospital electronic health record system. Signal sources include medication administrations, laboratory results, vital sign measurements, and clinical alerts (HL7 FHIR events).

Context: A hospital ICU generates millions of routine clinical events per day — scheduled medication doses, hourly vitals, routine lab draws. These are expected operational noise. Abnormal values, missed doses, and critical alerts are the signals that need attention.

The noise/pattern line: routine clinical measurements that repeat hourly are noise or pattern; abnormal values, missed doses, and deteriorating trends need attention.

Classify as exactly one of:
- routine_noise: scheduled/repeated measurement, no clinical significance change
- known_pattern: expected clinical event (medication given, routine lab result within range)
- needs_attention: abnormal value, missed dose, unusual trend, medication interaction risk
- real_incident: critical patient deterioration, medication error, code blue event

Answer with one word only."""

FEW_SHOT_EXEMPLARS = {
    "routine_noise": [
        "Heart Rate: 72 bpm (scheduled hourly vitals, within normal range)",
        "Temperature: 36.8 C (routine measurement, expected value)",
        "Blood Pressure: 118/76 (scheduled monitoring, stable)",
    ],
    "known_pattern": [
        "Potassium: 4.2 mEq/L (routine lab result, within reference range)",
        "Metoprolol 25mg administered (scheduled medication, on time)",
        "CBC panel ordered (standard daily lab draw)",
    ],
    "needs_attention": [
        "Potassium: 6.1 mEq/L (critical hyperkalemia, above reference range)",
        "Heparin dose missed (scheduled medication not administered)",
        "Heart Rate: 142 bpm (tachycardia, significant change from baseline)",
        "Creatinine: 3.8 mg/dL (acute kidney injury indicator, rising trend)",
    ],
    "real_incident": [
        "Patient in ventricular tachycardia (immediate clinical emergency)",
        "Medication error: 10x dose of insulin administered (patient safety event)",
        "Anaphylactic reaction to administered antibiotic (code blue called)",
    ],
}
