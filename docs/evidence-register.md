# Public evidence register

Reviewed 2026-11-02. **The staging gate is passed**: classification
78.1% on adjudicated cases, platform-pilot compression 92% with zero
dangerous misses. The release profile is `staging_qualified`. Private
observations transfer only through the candidate-bound
[proof handoff](proof-handoff.md); current priorities are in [roadmap.md](roadmap.md).

Cascade separates executable behavior, historical context, synthetic fixtures, and release proof.
The presence of a number in the repository does not make it a current performance or production
claim. This register identifies the authoritative evidence for each public claim class.

| Claim class | Public assertion allowed | Authoritative evidence | Current status |
|---|---|---|---|
| Engine behavior and contracts | The documented mechanics and fail-closed invariants exist | Source, schemas, and the tests that exercise the named invariant on the same commit | **Verified** — 1,420 tests, CI green |
| Staging-qualified artifact | The release installs, its tested mechanics execute, classification agreement is measured, and the supply chain is bound to one commit | Green CI/public-safety checks, attested candidate package and image, SBOM/provenance, and `config/release-profile.json` declaring `staging_qualified` | **Passed** — 0.2.0, 2026-11-02 |
| Classification effectiveness | 78.1% agreement on 160 adjudicated production cases (single-reviewer); 98% needs_attention recall; 4 dangerous | [Case study](case-study-platform-pilot.md); measurement harness and per-case results; platform-context fact extraction prompt revision | **Measured** — exceeds the 3-model judge panel (55.6%) and human panel (48.8%) |
| Compression effectiveness | 92% end-to-end elimination on a 24-hour platform pilot; three-arm comparison (817K observations); zero dangerous misses; 1,947 shadow re-checks, zero demotions | [Case study](case-study-platform-pilot.md); frozen-capture raw artifacts (private); three-arm comparison report | **Measured** — one platform, one window, workload-dependent |
| Cross-domain truth | BGL 100% recall · RT-IoT 88.6%/0% FP · AI4I 97.3% (value rules) · Backblaze 87.3%/86.7% · multi-sensory 100% · fraud 73.6%/82.5% · satellite 43% (boundary) | Evidence files in `local-evidence/flightpath-cross-domain-20261027/` (private); claims register in the production repo | **Verified** — nine domains, four boundaries mapped |
| Small-model qualification | Qwen2.5-3B: 100%/97.4% fact agreement with the 8B, 3.7GB RSS, 1.9x faster | Model-comparison evidence file (private); per-case results on the corpora PVC | **Verified** — qualifies as the event-lane model |
| Runtime cost | Model calls reduced from ~600/day to ~140/day on the pilot platform; template cache absorbs most fact-first residue | [Case study](case-study-platform-pilot.md); live service stats | **Measured** — one platform, estimated pricing |
| Governed staging health | Three-arm comparison, shadow validation, witnessed changes, GCL audit, graduated suppression tiers — all demonstrated live | Staging evidence package (private); witnessed-change records | **Verified** — all engineering items complete |
| Supply-chain identity | A wheel, source archive, SBOM, and container came from a specific candidate commit | Candidate manifest hashes, immutable image digest, GitHub attestations, and release workflow | **Verified** — v0.2.0 release with wheel artifact |
| Historical routing benchmark | The bundled router can consume a sanitized benchmark-shaped corpus | `config/corpora.json`, `data/benchmark_matrix.json`, and the metadata embedded in those files | Context only; not RC evidence |
| TCO scenarios | The calculator evaluates explicit assumptions | `data/hardware_profiles.json`, `data/model_profiles.json`, `data/workload_profiles.json`, and request assumptions | Synthetic assumptions; not customer economics |
