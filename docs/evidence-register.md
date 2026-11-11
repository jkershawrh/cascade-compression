# Public evidence register

## Published release and qualification

The published `v0.2.0` tag points to commit
`6800cd270a5e14cd419c65c760048c3fda8c5617`. Its GitHub release lists one
asset, `cascade_compression-0.2.0-py3-none-any.whl`, with SHA-256
`93582eb2b5e29a14b598be15be7808002439dd9fd6dc786d0b7e7ac88f3950c1`.
The digest identifies the published bytes. Its wheel metadata declares
12 domain plugins and eight collectors. [GitHub release](https://github.com/jkershawrh/cascade-compression/releases/tag/v0.2.0)

**Formal staging qualification is unproven.** The tagged profile declared
`staging_qualified`. CI failed locked dependency installation and the
installed-wheel smoke's seven-domain assertion. Release verification failed
at locked installation, and GitHub skipped the evidence, container and release
jobs. There is no verified, attested `staging_success` handoff binding the
published wheel, source archive, image, SBOM and evaluated run to that commit.
[Tag CI](https://github.com/jkershawrh/cascade-compression/actions/runs/41545535769)
· [Release workflow run](https://github.com/jkershawrh/cascade-compression/actions/runs/41545535005)

The local working candidate is `0.2.1rc1` with the `incubating_oss`
claim level. Incubating publication requires a frozen commit, green CI
and the attested candidate package and image. Staging qualification requires independent adjudication,
the exact tested artifacts and the complete attested handoff.
[Release policy](../RELEASING.md) · [Proof handoff](proof-handoff.md)

## What the historical evidence supports

| Evidence | Bounded observation | Limit |
|---|---|---|
| Deterministic replay | On 817,479 observations, rc7 retained 748,787 (8.4% suppressed). The simple filter retained 5,930. Of 215 recurring events, the filter missed 145 final counts and rc7 missed zero. | One frozen capture. The metric measures count preservation. Incident outcomes and inference savings were outside the replay. [Case study](case-study-platform-pilot.md) |
| Operational report | A separate pilot summary estimated that ~620K of ~7.4M observations reached inference (~92% avoided). It also reported model-call and shadow-check counters. | Historical estimates and operational observations use their own denominators. Matched cost, incident safety and release effectiveness remain unestablished. [Case study](case-study-platform-pilot.md) |
| Tuning | The platform-context prompt agreed on 125/160 cases (78.1%) with four dangerous classification errors. | Single-reviewer development set reused for tuning. [Case study](case-study-platform-pilot.md) |
| Held-out classification | On 100 cases the classifier got 84/84 needs_attention and 0/16 known_pattern correct, tying the always-needs_attention baseline at 84%. | Document-derived reference labels. No real_incident or routine_noise cases. [Case study](case-study-platform-pilot.md) |
| Domain experiments | BGL 100% anomaly recall. RT-IoT 88.6% attack recall, 0% normal false positives. AI4I value rules 97.3% recall. Backblaze 87.3% failure-day, 86.7% lead-up. Multi-sensory rules resolved the seeded corpus deterministically. Fraud recall 73.6% with rules, 82.5% combined. Satellite holdout recall 43%. | Separate corpora, truth sources and experiments. Each result applies to its tested configuration. |
| Model comparison | Qwen2.5-3B had 100% and 97.4% fact agreement with the 8B comparator on two corpora, 3.7 GB RSS and 1.9× throughput at equal CPU. | Model-to-model agreement measures agreement with a comparator. |
| Engine and governance | Source, schemas and focused tests exercise named mechanisms. Historical pilot observations include shadow rechecks and witnessed authority changes. | A passing check covers the tested revision and behavior. Staging health requires the complete candidate-bound run. [Staging evidence](staging-evidence.md) |
| Historical routing benchmark | The router can consume the sanitized benchmark-shaped corpus in `config/corpora.json` and `data/benchmark_matrix.json`. | Historical context. |
| TCO scenarios | The calculator uses `data/hardware_profiles.json`, `data/model_profiles.json`, `data/workload_profiles.json` and request assumptions. | Synthetic scenarios. Realized savings require observed costs and a matched baseline. |
