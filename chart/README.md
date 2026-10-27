# Cascade Compression - Helm Chart

## Quick start

```bash
# minimal deployment (single service, no collectors)
helm install cascade ./chart \
  --set llm.url=https://your-llm-gateway.example.com \
  --set llm.key=your-api-key \
  --set llm.model=granite-3-2-8b-instruct

# verify
kubectl wait --for=condition=ready pod -l app=cascade-aap --timeout=120s
kubectl exec deploy/cascade-aap -- curl -s localhost:8090/health
# {"status": "ok", "nano_profile": "qualified_repeat_update"}

# watch it learn (agents appear as evidence accumulates)
kubectl exec deploy/cascade-aap -- curl -s localhost:8090/agents

# check compression
kubectl exec deploy/cascade-aap -- curl -s localhost:8090/stats | jq .stats.compression_ratio
```

## Values

| Key | Default | Description |
|---|---|---|
| `image.repository` | `cascade-compression/cascade` | image repository |
| `image.tag` | `latest` | image tag |
| `llm.url` | **required** | OpenAI-compatible LLM endpoint |
| `llm.key` | **required** | API key |
| `llm.model` | **required** | model name for classification |
| `nanoProfile` | `qualified_repeat_update` | suppression profile |
| `dedupWindow` | `86400` | default dedup window (seconds) |
| `services.aap.enabled` | `true` | deploy the AAP signal service |
| `services.k8s.enabled` | `true` | deploy the K8s signal service |
| `services.knowledge.enabled` | `false` | deploy the knowledge service |
| `services.memory.enabled` | `false` | deploy the memory aggregator |
| `collectors.enabled` | `false` | deploy signal collectors |
| `ledger.enabled` | `false` | deploy the immutable ledger + GCL audit |
| `state.storageClass` | cluster default | PVC storage class for learning state |
| `state.size` | `5Gi` | PVC size |

## What you get

- The cascade engine with **qualified learned suppression** (signals are
  suppressed only after earning authority: 200+ classified noise samples
  with zero important findings)
- **Graduated suppression tiers** — deeper evidence earns deeper suppression
  (first-3/5min → first-1/5min → first-3/hour)
- **Shadow validation** — 5% of suppressed signals re-checked; disagreement
  triggers instant withdrawal
- **Severity fail-open** — high/critical never suppressed; protected labels
  (compliance/fraud/sanctions) always escalated
- **Time-bounded authority** — 72h activation TTL; types must re-qualify
- **Truth-informed few-shot prompts** — the classifier carries adjudicated
  exemplars that teach it the platform's operating rhythm

## Safety guarantees (structural, not classifier-dependent)

1. The first occurrence of any signal identity is always delivered
2. High and critical severity signals are never suppressed
3. Protected labels are escalated before any suppressor sees them
4. Any single important classification instantly withdraws suppression
   authority for that type
5. Every suppression decision is receipted and auditable

## Learning state

The learning state (promoted types, evidence counts, suppression tiers)
persists on a PVC and survives restarts. On restart, types re-qualify
through the fresh-evidence pathway (historical evidence informs but does
not confer authority). State growth is bounded by the memory archive
capacity (default 10K memories).
