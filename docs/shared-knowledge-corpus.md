# Shared Knowledge Corpus

Auto-extracted from 36 projects, 1,579 claims. Topics appearing in 3+ projects.

## Maas Inference (25 projects)

**Rules:**
- phi-4 via LiteLLM (maas-rhdp) is the right backend for Gate 2 classification. The zero-shot encoder (distilbert-mnli) guesses at 30-46% confidence. phi-4 classifies decisively at 270-314ms with the ri
- LiteLLM endpoint: `LITELLM_API_BASE=https://maas-rhdp.apps.maas.redhatworkshops.io`
- Replace the encoder-service HTTP calls in `_run_gate2()` with LiteLLM calls to phi-4. The domain pack YAML provides the system prompt context. The encoder pod can be scaled down or repurposed.

[[proj
- In all showroom labs, demo frontends, READMEs, and presentation materials: use "Powered by Intel" or "Intel Xeon" / "Intel Xeon 6". The MaaS model map may reference `gpt-oss-120b-gaudi` as a technical
- Explain the WHY before the HOW (why vLLM on CPU, why this architecture pattern)

**Key facts:**
- Focus is CPU-only (Xeon 6) for now. Gaudi 3 GPU support is future scope. Changes to the planner are data-driven (gpu_catalog.json, slo_templates.json, benchmark data) rather than code changes. A stand
- Hardware detection method: LiteLLM `/v1/model/info` shows backend `api_base` URLs.
- `-external-` route + `-cpu` suffix = Xeon 6 (OpenVINO runtime)
- `-direct-` route (no `-cpu`) = Gaudi 3 (vLLM runti
- granite-2b-cpu → `granite-2b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`

*Sources: InferencePlan, StarGate, agentobs, agentops-in-prod-aut, ai-sovereignty, crawler...*

---

## Openshift Deploy (25 projects)

**Rules:**
- The system is still being validated. User is building toward auto-remediation but is not there yet. Read-only diagnostics (oc get/describe/logs) are fine. Any mutation (oc rollout restart, oc delete, 
- Requires namespace-specific claims naming the sandbox and failure cause
- Show trust boundaries (TDX enclave, namespace isolation, policy gates)
- Before any deploy to infra01:
1. Verify the deployment has an oauth-proxy sidecar container
2. Verify the route targets the oauth-proxy port (8080/8443/4180), not the backend directly
3. Test with `cu
- 1. **No work during European hours** — cluster is shared with European teams. Only use outside EU business hours.
2. **Always verify nothing is running before starting** — check `oc get pods --all-nam

**Key facts:**
- granite-2b-cpu → `granite-2b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- phi3-mini-cpu → `phi3-mini-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`
- qwen25-3b-cpu → `qwen25-3b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`

*Sources: InferencePlan, StarGate, agentobs, agentops-in-prod-aut, ai-sovereignty, are-immutable-ledger...*

---

## Demo Platform (23 projects)

**Rules:**
- Don't create the agnosticv config manually. Run the liftoff onboard pipeline first to right-size the deployment — it scans the repo, classifies it, generates the agnosticv config, and validates readin
- The liftoff pipeline (at /Users/jkershaw/Documents/liftoff) is the standard path from repo → RHDPS-ready. It runs NovaScan (capacity), DarkScope (security), and LLM classification to generate the corr
- 1. Finish the Antora showroom content (content/ directory with lab pages)
2. Run `liftoff onboard` against the sovereign-ai-lab repo
3. Review the generated agnosticv config
4. Adjust if needed, then 
- phi-4 via LiteLLM (maas-rhdp) is the right backend for Gate 2 classification. The zero-shot encoder (distilbert-mnli) guesses at 30-46% confidence. phi-4 classifies decisively at 270-314ms with the ri
- LiteLLM endpoint: `LITELLM_API_BASE=https://maas-rhdp.apps.maas.redhatworkshops.io`

**Key facts:**
- llm-d-planner (cloned to InferencePlan/llm-d-planner/) is being adapted as a CPU inference capacity planner for the Intel Red Hat partner demo platform.
- The demo platform has no capacity planning — replicas, concurrency, and rate limits are all hardcoded. The planner provides SLO-driven sizing backed by real benchmark data.
- **StarGate** (namespace: stargate) — batch readiness scanner + remediation pipeline
- API: 2 replicas + oauth-proxy, Frontend: nginx + oauth-proxy, Scanner: scheduler + babylon-worker, Postgres
- Scan

*Sources: InferencePlan, Multimodal-DeepField, StarGate, agentobs, agentops-in-prod-aut, ai-sovereignty...*

---

## Granite Xeon (23 projects)

**Rules:**
- All work should reinforce "one model, one silicon" — granite-3-2-8b-instruct on Xeon 6 only. No Gaudi references in new work. Branding must be Intel + Red Hat co-branded.

Related: [[reference-repos]]
- Use "Powered by Intel" as the brand message. Do NOT reference "Intel Gaudi" in branding. "Intel Xeon 6" is acceptable.
- In all showroom labs, demo frontends, READMEs, and presentation materials: use "Powered by Intel" or "Intel Xeon" / "Intel Xeon 6". The MaaS model map may reference `gpt-oss-120b-gaudi` as a technical
- Intel Hardware section on the index page (component → Intel Xeon table)
- Don't fold RACM infrastructure optimization into the porting work. The two tracks are: (1) port quickstarts to showroom + benchmark MaaS models (our pipeline), (2) optimize how RACM serves models on X

**Key facts:**
- Focus is CPU-only (Xeon 6) for now. Gaudi 3 GPU support is future scope. Changes to the planner are data-driven (gpu_catalog.json, slo_templates.json, benchmark data) rather than code changes. A stand
- Hardware detection method: LiteLLM `/v1/model/info` shows backend `api_base` URLs.
- `-external-` route + `-cpu` suffix = Xeon 6 (OpenVINO runtime)
- `-direct-` route (no `-cpu`) = Gaudi 3 (vLLM runti
- granite-2b-cpu → `granite-2b-cpu-external-llm-hosting.apps.ocp-rac-maas.rs-dfw3.infra.demo.redhat.com`

*Sources: InferencePlan, StarGate, agentobs, ai-sovereignty, command-center, crawler...*

---

## Methodology (17 projects)

**Rules:**
- Follow the CDD then TDD then BDD then EDD methodology with red/green rubric grids for all milestones.
- Every milestone must have contracts defined before logic, tests written before implementation (red first), BDD Given/When/Then scenarios, and an EDD rubric grid. verify.sh must exit 0 before a milesto
- Don't just write code and move on — follow a TDD/EDD red/green cycle. Run the verification tests first to see what's RED, then work to make each check GREEN.
- For each document phase:
1. Write the tests/verification first (or use the ones already specified in the docs)
2. Run them — see the red/green matrix
3. Fix what's red, one check at a time
4. Report t
- Always include a multi-stage gated validation matrix (CDD → TDD → EDD → BDD → CBT → Publication) with claim registries for provenance tracking.

**Key facts:**
- All work for the multimodal agent pack targets the `deepfield-multimodal` repo. The original `deepfield` repo is read-only reference for architectural patterns. The spec lives at `/Users/jkershaw/Docu
- **Deepfield EDD rubrics still show some FAILING** — routing_quality and fleet_health need the inference router subsystem (not installed) or more sophisticated fallback
- The red/green verification matrix has "Local" (Mac, simulated) and "Target" (Folsome, real) columns

*Sources: Multimodal-DeepField, StarGate, agent-promotion, ai-sovereignty, crawler, edge-inference-at-sc...*

---

## Self Serve Quickstart (16 projects)

**Rules:**
- Don't create the agnosticv config manually. Run the liftoff onboard pipeline first to right-size the deployment — it scans the repo, classifies it, generates the agnosticv config, and validates readin
- The liftoff pipeline (at /Users/jkershaw/Documents/liftoff) is the standard path from repo → RHDPS-ready. It runs NovaScan (capacity), DarkScope (security), and LLM classification to generate the corr
- 1. Finish the Antora showroom content (content/ directory with lab pages)
2. Run `liftoff onboard` against the sovereign-ai-lab repo
3. Review the generated agnosticv config
4. Adjust if needed, then 
- When building a new project or quickstart, create `tests/validation_matrix.yaml`, `tests/claim_registry.yaml`, and `tests/benchmark_rubric.yaml` following the pattern in edge-ai-cpu-inference. Structu
- Explicit direction from the user regarding Intel partner branding requirements for RHDP showrooms and quickstart materials.

**Key facts:**
- Focus is CPU-only (Xeon 6) for now. Gaudi 3 GPU support is future scope. Changes to the planner are data-driven (gpu_catalog.json, slo_templates.json, benchmark data) rather than code changes. A stand
- Application repo: `rh-ai-quickstart/multi-agent-loan-origination` (Python agents, LangGraph)
- Four major workstreams (equal priority):
1. **AI Quickstarts** — Intel quickstart factory, showroom ports, RHDP pipeline, XDD test suites
2. **Triforce** — episodic labs (6 episodes, 59 pages), Intel 

*Sources: InferencePlan, agentops-in-prod-aut, ai-sovereignty, command-center, dev-audit, edge-inference-at-sc...*

---

## Governance (16 projects)

**Rules:**
- Executives and government stakeholders need to see how the pieces connect visually. A schematic shows trust boundaries, data flow direction, which components talk to which, and where governance checkp
- Lead with sovereignty, not governance mechanics
- The ecosystem whitepaper must be strategically framed: fleet-llm-d is the Red Hat product being showcased. deepfield-fleet, governed-cognitive-loop, and are-immutable-ledger are Kersh's creations that
- Lead every section with what fleet-llm-d does. Frame the other systems as "what fleet-llm-d's architecture enables" rather than equal peers. The narrative is: fleet-llm-d provides the fleet orchestrat
- All work should reinforce "one model, one silicon" — granite-3-2-8b-instruct on Xeon 6 only. No Gaudi references in new work. Branding must be Intel + Red Hat co-branded.

Related: [[reference-repos]]

**Key facts:**
- The user decided to fully open-source the ledger (no commercial holdback) to maximize AAIF credibility. ARE Foundation passports, scope, policy, and the full governance stack remain as-is.
- `srex-dev/are-foundation` — the full ARE Foundation (S0/S1 governance)
- **GeoLux** (namespace: geolux) — governance brain
- Receives evidence from StarGate (HTTP + Kafka), generates hypotheses, classifies, runs MPC
- Hypothesis dedup: in-memory cache (1h TTL) + DB check p

*Sources: OpenShell, StarGate, agentobs, ai-sovereignty, are-foundation, command-center...*

---

## Infra01 Only (14 projects)

**Rules:**
- Every application deployed to infra01 (ocpv-infra01) MUST have Red Hat OAuth proxy (ose-oauth-proxy-rhel9 sidecar) on all externally-routed frontend and API deployments.
- Before any deploy to infra01:
1. Verify the deployment has an oauth-proxy sidecar container
2. Verify the route targets the oauth-proxy port (8080/8443/4180), not the backend directly
3. Test with `cu
- When rebasing agnosticv PR branches, the context keeps switching back to infra01. Always `oc config use-context` explicitly before running oc commands on integration or prod clusters.
- This project deploys to infra01 ONLY. Before any oc command, verify context:
- Multiple sessions have accidentally run commands against Oberon (default/api-oberon-fm2aihpcsed-com:6443/kube:admin) instead of infra01, causing "namespace not found" errors, stale data reads, and gat

**Key facts:**
- Three systems deployed on infra01 (ocpv-infra01.dal12.infra.demo.redhat.com), all scoped to 5 sandbox clusters: ocpv05, ocpv06, ocpv07, ocpv08, ocpv09.
- GeoLux is the governance brain connecting Deepfield (detection) and StarGate (execution). Deployed on infra01 namespace `geolux`.
- Scanner clusters reduced to ocpv05-09 only (removed infra01, infra02, ocp-us-east-1)

*Sources: StarGate, ai-sovereignty, crawler, deepfield, fleet-llm-d, intel-partnership-pm...*

---

## Summit Connect (12 projects)

**Rules:**
- When building a new project or quickstart, create `tests/validation_matrix.yaml`, `tests/claim_registry.yaml`, and `tests/benchmark_rubric.yaml` following the pattern in edge-ai-cpu-inference. Structu
- Explicit direction from the user regarding Intel partner branding requirements for RHDP showrooms and quickstart materials.
- Kelkhund (org gatekeeper) explicitly stated this when granting org access. The quickstart catalog was originally ranked by Intel tech story + originality, but the org cares most about the business pro
- Showroom labs are NOT "deploy the quickstart and explore the UI." They teach the user how to BUILD what the quickstart builds, step by step, so they understand the technology contextually.
- Package Launchpad as AgnosticV config + AgnosticD roles. Don't change core Launchpad architecture (namespace-per-demo). The RHDP integration is about deployment packaging, not internal restructuring.


**Key facts:**
- Application repo: `rh-ai-quickstart/multi-agent-loan-origination` (Python agents, LangGraph)
- Four major workstreams (equal priority):
1. **AI Quickstarts** — Intel quickstart factory, showroom ports, RHDP pipeline, XDD test suites
2. **Triforce** — episodic labs (6 episodes, 59 pages), Intel 
- Demonstrate that even with 2G connectivity and minimal hardware (no GPU), you can deploy useful AI inference at edge sites — disaster zones, warzones, underserved communities. Summit Connect conferenc

*Sources: agentops-in-prod-aut, command-center, edge-inference-at-sc, intel-partnership-pm, intel-quickstarts-tr, launchpad...*

---

## Immutable Ledger (11 projects)

**Rules:**
- The sovereign AI lab demo story is about **national/organizational AI sovereignty** — the ability to possess, control, adapt, govern, and prove your AI infrastructure is truly yours. The immutable led
- Show that sovereign AI means more than local deployment — it requires provable control recorded in a tamper-evident immutable ledger (are-immutable-ledger).
- The ecosystem whitepaper must be strategically framed: fleet-llm-d is the Red Hat product being showcased. deepfield-fleet, governed-cognitive-loop, and are-immutable-ledger are Kersh's creations that

**Key facts:**
- ARE Foundation's immutable ledger has been extracted into a standalone repo at jkershawrh/are-immutable-ledger (GitHub). Positioned as neutral infrastructure for cross-system agentic proof chains.
- `jkershawrh/are-immutable-ledger` — the standalone ledger + demo
- Key changes deployed:
- GCL prompt governance adapter replaced regex semantic router (evidence→classify→falsify→sign→commit)
- OPA decisions now bridge to the immutable ledger via demo-api
- Periodic 

*Sources: OpenShell, agent-promotion, ai-sovereignty, are-foundation, command-center, crawler...*

---

## Testing Coverage (10 projects)

**Rules:**
- Save for next phase. Requires careful extraction to avoid breaking deepfield's 231 tests. The nanoagents need a standalone signal model (dict-based, no Pydantic deepfield imports) similar to how the b

**Key facts:**
- All work for the multimodal agent pack targets the `deepfield-multimodal` repo. The original `deepfield` repo is read-only reference for architectural patterns. The spec lives at `/Users/jkershaw/Docu
- Four major workstreams (equal priority):
1. **AI Quickstarts** — Intel quickstart factory, showroom ports, RHDP pipeline, XDD test suites
2. **Triforce** — episodic labs (6 episodes, 59 pages), Intel 
- **intel-inference-router**: cascade/, 248 tests, Helm chart

*Sources: Multimodal-DeepField, ai-sovereignty, command-center, crawler, intel-partnership-pm, intel-tco-calculator...*

---

## Branding (10 projects)

**Rules:**
- Red Hat logo SVGs loaded via `<img>` tags MUST use inline `fill` attributes, not `<style>` CSS classes — browsers ignore internal stylesheets in SVGs loaded as images.
- Download official SVGs from `static.redhat.com/libs/redhat/brand-assets/2/corp/`
- `logo.svg` — standard (black wordmark, for light backgrounds)
- On dark mastheads: use white pill container (`background: white, padding: 6px 14px, borderRadius: 6px`) with the standard (black text) logo inside — per brand guidelines
- All work should reinforce "one model, one silicon" — granite-3-2-8b-instruct on Xeon 6 only. No Gaudi references in new work. Branding must be Intel + Red Hat co-branded.

Related: [[reference-repos]]

**Key facts:**
- Multi-agent orchestration system to be deployed on OpenShift AI (Red Hat AI 3.4+). Inference on Intel Xeon 6 and Gaudi accelerators. Will use Red Hat's AgentOps stack: OpenTelemetry distributed tracin
- Works at Red Hat on the demo platform. Building a multi-agent orchestration system on OpenShift AI. Has access to Intel Xeon 6 and Gaudi accelerator servers for inference. May be able to provision Ope
- Critical correction from Ashok Jammula on 2026-06-16. The demo narrative assumed granite-3-2-8b-instruct runs on Intel Xeon 6 — it does NOT.

*Sources: agentobs, deepfield, intel-partnership-pm, intel-quickstarts-tr, intel-tco-calculator, launchpad...*

---

## Agent Architecture (6 projects)

**Key facts:**
- Multi-agent orchestration system to be deployed on OpenShift AI (Red Hat AI 3.4+). Inference on Intel Xeon 6 and Gaudi accelerators. Will use Red Hat's AgentOps stack: OpenTelemetry distributed tracin
- Works at Red Hat on the demo platform. Building a multi-agent orchestration system on OpenShift AI. Has access to Intel Xeon 6 and Gaudi accelerator servers for inference. May be able to provision Ope
- Application repo: `rh-ai-quickstart/multi-agent-loan-origination` (Python agents, LangGraph)

*Sources: agentobs, agentops-in-prod-aut, command-center, intel-partnership-pm, launchpad, triforce*

---

## Deterministic (4 projects)

**Key facts:**
- **Constraint Classification** — 32 constraints, deterministic assertions
- Extract the 19 deterministic nanoagents from deepfield into intel-inference-router so any consumer gets cascade + routing + scaling in one pip install.
- 100% deterministic FAILS (severity check flags medium+ drops) — LLM adversary probe not yet wired into standalone sampler

*Sources: StarGate, crawler, intel-tco-calculator, slo-sli-automation*

---

## Pipeline Routing (4 projects)

**Key facts:**
- intel-inference-router has routing/corpora/bootstrapper/scaler/fleet_manager
- Any new consumer gets the full 85% compression + five-lane routing in one package
- ```
deepfield (monolith)
  nanoagents/ (19 agents) → FilterDecision
  routing/signal_router.py → imports intel-inference-router
  
intel-inference-router (package)
  corpora, lanes, bootstrapper, scal

*Sources: crawler, intel-tco-calculator, red-hat-intel-partne, 2026*

---

## Intel Partnership (4 projects)

**Rules:**
- Explicit direction from the user regarding Intel partner branding requirements for RHDP showrooms and quickstart materials.
- The cascade framework (`cascade_compression/`) must never be modified for domain-specific work. Domain packs are the adapter layer. Related: [[people-ron-haberman]], [[project-tco-calculator]]
- Ron Haberman (AI Incubation, Red Hat) needs dollar-per-business-process TCO numbers for customer conversations with Amex. The story: cascade compression means most signals don't need a model, so cheap

**Key facts:**
- **Corpora compiled from real benchmarks**: 216 entries from 16 benchmark files → 19 routing entries across 6 industries, 21 quality gaps. Key picks: granite-350m for classification, smollm2-360m for b
- Every feature/fix should reinforce the cascade compression narrative. Throughput numbers are placeholders until mtahhan delivers RHAIIS 3.5 benchmarks.

Key people: [[people-ron-haberman]], [[people-s
- The Intel partner demo needs to be packaged as a Catalog Item (CI) using agnosticd/agnosticv — the standard Red Hat demo delivery pipeline. Workflow: devel branch → integration environment → prod bran

*Sources: crawler, intel-quickstarts-tr, intel-tco-calculator, red-hat-intel-partne*

---

## Story First (3 projects)

**Rules:**
- Don't build dashboards with tabs/panels. Build guided story experiences using the act-based pattern from Triforce and DeepField Multimodal.
- Jonathan's demos are presented on large projection screens to executive audiences. A dashboard with tiny text and tabs doesn't tell a story. The hero's journey arc (ordinary world → call → ordeal → re
- Projection-scale fonts: 56-80px headlines, 20-28px body, min 14px for anything
- Progressive disclosure: one thought per click, never a wall of text
- Reference: Triforce App.tsx (act engine), DeepField App.tsx (hero's journey mapping)

**Key facts:**
- **Navigation:** Act-based linear progression with progress dots and keyboard nav.
- Hero's Journey stays but the punchline changes: "80% CPU at $0, 20% GPU at $/token, system routes for you"

*Sources: ai-sovereignty, intel-quickstarts-tr, triforce*

---

## Corpus Statistics

- **Projects scanned:** 36
- **Claims extracted:** 1579
- **Institutional claims:** 653 (41%)
- **Project-specific claims:** 926 (58%)
- **Institutional topics:** 17
- **Rules discovered:** 174
