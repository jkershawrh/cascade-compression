# The Memory of RHDP — What the Platform Remembers

**Generated:** 2026-08-14 22:45 UTC
**Source:** infra01 — 5 OpenShift clusters + Ansible Automation Platform (prod0)
**Architecture:** Cascade compression with federated memory formation

This document is the institutional memory of the Red Hat Demo Platform,
formed automatically by the cascade compression engine. Every entry below
is a signal that survived three layers of compression: the cascade pipeline,
content-hash deduplication, and federated consolidation. What remains is
what actually mattered.

---

## Platform Overview

| Instance | Memories | Evictions | Avg Strength | Max Strength | Fed. Sources |
|----------|----------|-----------|-------------|-------------|-------------|
| cascade-k8s | 1,311 | 0 | 0.732 | 0.985 | 1 |
| cascade-aap | 4,574 | 0 | 0.218 | 0.831 | 1 |
| aggregator | 862 | 753 | 0.822 | 1.000 | 2 |

**Total memories retained:** 6,747
**Total evictions (noise removed):** 753
**Selectivity:** 10.0% of all signals were forgotten

---

## Kubernetes Clusters (5 OCP clusters)

*1311 memories | 0 evictions | avg strength 0.732*

### Severity Distribution

- **medium**: 1,311 (100.0%) ██████████████████████████████████████████████████

### What the Platform Remembers

| Signal Type | Count | Avg Strength | Min | Max | Primary Severity |
|-------------|-------|-------------|-----|-----|-----------------|
| `event_deprecatedannotation` | 583 | 0.566 | 0.400 | 0.975 | medium |
| `event_volumefaileddelete` | 162 | 0.961 | 0.400 | 0.985 | medium |
| `event_claimmisbound` | 143 | 0.941 | 0.460 | 0.985 | medium |
| `event_failedtoretrieveimagepullsecret` | 78 | 0.546 | 0.400 | 0.606 | medium |
| `event_ipaddresswrongreference` | 65 | 0.902 | 0.400 | 0.985 | medium |
| `event_unhealthy` | 60 | 0.727 | 0.400 | 0.985 | medium |
| `event_unrecognizeddatasourcekind` | 49 | 0.956 | 0.400 | 0.985 | medium |
| `event_failedscheduling` | 21 | 0.983 | 0.952 | 0.985 | medium |
| `event_resolutionfailed` | 21 | 0.622 | 0.400 | 0.985 | medium |
| `event_provisioningfailed` | 20 | 0.940 | 0.646 | 0.985 | medium |
| `event_failedattachvolume` | 18 | 0.935 | 0.514 | 0.985 | medium |
| `event_backoff` | 18 | 0.558 | 0.400 | 0.713 | medium |
| `event_completed` | 17 | 0.954 | 0.563 | 0.985 | medium |
| `event_error` | 13 | 0.940 | 0.400 | 0.985 | medium |
| `event_unschedulable` | 10 | 0.975 | 0.889 | 0.985 | medium |
| `event_volumeresizefailed` | 9 | 0.908 | 0.606 | 0.985 | medium |
| `event_pending` | 9 | 0.943 | 0.606 | 0.985 | medium |
| `event_failedmount` | 5 | 0.985 | 0.985 | 0.985 | medium |
| `event_backofflimitexceeded` | 3 | 0.985 | 0.985 | 0.985 | medium |
| `event_stopped` | 3 | 0.718 | 0.563 | 0.985 | medium |

### Core Memories (highest strength — what matters most)

1. **[medium]** strength=0.985
   `event_backofflimitexceeded` — Job has reached the specified backoff limit
   *namespace: ccm-monitoring*
1. **[medium]** strength=0.985
   `event_backofflimitexceeded` — Job has reached the specified backoff limit
   *namespace: ccm-monitoring*
1. **[medium]** strength=0.985
   `event_backofflimitexceeded` — Job has reached the specified backoff limit
   *namespace: ccm-monitoring*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_volumeresizefailed` — mark PVC "cnv-images/tmp-pvc-8c15aced-31b5-4106-8b91-7a3307f12e8e" as resize finished fail
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=0.985
   `event_volumefaileddelete` — rpc error: code = Aborted desc = rbd csi-vol-143fc256-eeb4-4481-b5aa-43a6ed531d6c is still
   *namespace: default*

### Fading Memories (weakest — about to be forgotten)

- str=0.400 `event_failedtoretrieveimagepullsecret` — Unable to retrieve some image pull secrets (search-pull-secret); attempting to pull the im
- str=0.400 `event_failedtoretrieveimagepullsecret` — Unable to retrieve some image pull secrets (search-pull-secret); attempting to pull the im
- str=0.400 `event_deprecatedannotation` — Service uses deprecated annotation metallb.universe.tf/allow-shared-ip
- str=0.400 `event_failedtoretrieveimagepullsecret` — Unable to retrieve some image pull secrets (search-pull-secret); attempting to pull the im
- str=0.400 `event_failedtoretrieveimagepullsecret` — Unable to retrieve some image pull secrets (search-pull-secret); attempting to pull the im
- str=0.400 `event_unrecognizeddatasourcekind` — The datasource for this PVC does not match any registered VolumePopulator
- str=0.400 `event_error` — source/target size info missing
- str=0.400 `event_unhealthy` — Readiness probe failed: Get "https://10.130.68.161:6443/healthz": net/http: request cancel
- str=0.400 `event_deprecatedannotation` — Service uses deprecated annotation metallb.universe.tf/allow-shared-ip
- str=0.400 `event_volumefaileddelete` — persistentvolume pvc-49c0410d-db31-45f1-ba78-ded10ad50f04 is still attached to node ocp-vi

### Where the Memories Come From

- `default`: 248 (18.9%) █████████
- `cnv-images`: 103 (7.9%) ███
- `open-cluster-management`: 88 (6.7%) ███
- `sandbox-4zvww-ocp4-cluster`: 75 (5.7%) ██
- `sandbox-884rh-ocp4-cluster`: 66 (5.0%) ██
- `sandbox-4wgls-ocp4-cluster`: 64 (4.9%) ██
- `sandbox-2nlhq-ocp4-cluster`: 60 (4.6%) ██
- `sandbox-426rj-ocp4-cluster`: 27 (2.1%) █
- `sandbox-4l499-ocp4-cluster`: 27 (2.1%) █
- `kubernetes-secret-generator`: 23 (1.8%) 
- *...and 56 more namespaces*

---

## Ansible Automation Platform (prod0)

*4574 memories | 0 evictions | avg strength 0.218*

### Severity Distribution

- **high**: 93 (2.0%) █
- **medium**: 244 (5.3%) ██
- **low**: 4,100 (89.6%) ████████████████████████████████████████████
- **info**: 137 (3.0%) █

### What the Platform Remembers

| Signal Type | Count | Avg Strength | Min | Max | Primary Severity |
|-------------|-------|-------------|-----|-----|-----------------|
| `task_runner_on_skipped` | 4,100 | 0.200 | 0.200 | 0.280 | low |
| `task_warning` | 242 | 0.400 | 0.400 | 0.400 | medium |
| `task_runner_on_failed` | 74 | 0.700 | 0.700 | 0.700 | high |
| `task_verbose` | 71 | 0.100 | 0.100 | 0.100 | info |
| `task_runner_on_ok` | 56 | 0.100 | 0.100 | 0.100 | info |
| `task_playbook_on_stats` | 14 | 0.700 | 0.700 | 0.700 | high |
| `task_runner_item_on_ok` | 10 | 0.100 | 0.100 | 0.100 | info |
| `task_runner_item_on_failed` | 4 | 0.700 | 0.700 | 0.700 | high |
| `config_update_inventory` | 1 | 0.831 | 0.831 | 0.831 | medium |
| `config_update_credential` | 1 | 0.812 | 0.812 | 0.812 | medium |
| `task_error` | 1 | 0.700 | 0.700 | 0.700 | high |

### Core Memories (highest strength — what matters most)

1. **[medium]** strength=0.831
   `config_update_inventory` — update inventory
   *namespace: aap-config*
1. **[medium]** strength=0.812
   `config_update_credential` — update credential
   *namespace: aap-config*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Get a list of clusters on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Get vSphere login session on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_playbook_on_stats` —  on 
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Get a list of clusters on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Get a list of clusters on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_playbook_on_stats` —  on 
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Get vSphere login session on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Create OpenShift cluster using Assisted Installer on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Get a list of clusters on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Delete Azure DevOps (Visual Studio) account resources on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_item_on_failed` — Delete Azure DevOps (Visual Studio) account resources on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_runner_on_failed` — Get a list of clusters on localhost
   *namespace: aap-general*
1. **[high]** strength=0.700
   `task_playbook_on_stats` —  on 
   *namespace: aap-general*

### Fading Memories (weakest — about to be forgotten)

- str=0.100 `task_verbose` — Configure NetworkPolicy on 
- str=0.100 `task_runner_on_ok` — Lock Bastion Security Group (CNV) on localhost
- str=0.100 `task_verbose` — Lock Bastion Security Group (EC2) on 
- str=0.100 `task_verbose` — Lock Bastion Security Group (CNV) on 
- str=0.100 `task_runner_on_ok` — Configure NetworkPolicy on localhost
- str=0.100 `task_verbose` — Configure NetworkPolicy on 
- str=0.100 `task_verbose` — Lock Bastion Security Group (EC2) on 
- str=0.100 `task_runner_on_ok` — Lock Bastion Security Group (CNV) on localhost
- str=0.100 `task_runner_on_ok` — Configure NetworkPolicy on localhost
- str=0.100 `task_verbose` — Lock Bastion Security Group (CNV) on 

### Where the Memories Come From

- `aap-general`: 4572 (100.0%) █████████████████████████████████████████████████
- `aap-config`: 2 (0.0%) 

---

## Federated Aggregator (cross-domain)

*862 memories | 753 evictions | avg strength 0.822*

### Severity Distribution

- **high**: 48 (5.6%) ██
- **medium**: 814 (94.4%) ███████████████████████████████████████████████

### What the Platform Remembers

| Signal Type | Count | Avg Strength | Min | Max | Primary Severity |
|-------------|-------|-------------|-----|-----|-----------------|
| `event_volumefaileddelete` | 159 | 0.992 | 0.692 | 1.000 | medium |
| `event_claimmisbound` | 143 | 0.988 | 0.564 | 1.000 | medium |
| `event_deprecatedannotation` | 117 | 0.623 | 0.201 | 1.000 | medium |
| `task_warning` | 76 | 0.294 | 0.100 | 0.700 | medium |
| `event_ipaddresswrongreference` | 65 | 0.935 | 0.450 | 1.000 | medium |
| `event_unrecognizeddatasourcekind` | 49 | 0.997 | 0.850 | 1.000 | medium |
| `event_unhealthy` | 43 | 0.811 | 0.116 | 1.000 | medium |
| `task_runner_on_failed` | 40 | 0.555 | 0.100 | 1.000 | high |
| `event_failedscheduling` | 21 | 1.000 | 1.000 | 1.000 | medium |
| `event_provisioningfailed` | 20 | 1.000 | 1.000 | 1.000 | medium |
| `event_failedattachvolume` | 18 | 0.974 | 0.564 | 1.000 | medium |
| `event_completed` | 17 | 1.000 | 1.000 | 1.000 | medium |
| `event_failedtoretrieveimagepullsecret` | 16 | 0.385 | 0.205 | 0.613 | medium |
| `event_error` | 13 | 0.988 | 0.850 | 1.000 | medium |
| `event_unschedulable` | 10 | 1.000 | 1.000 | 1.000 | medium |
| `event_volumeresizefailed` | 9 | 0.962 | 0.658 | 1.000 | medium |
| `event_pending` | 9 | 1.000 | 1.000 | 1.000 | medium |
| `event_resolutionfailed` | 8 | 0.325 | 0.101 | 1.000 | medium |
| `event_failedmount` | 5 | 1.000 | 1.000 | 1.000 | medium |
| `event_backoff` | 4 | 0.433 | 0.259 | 0.613 | medium |

### Core Memories (highest strength — what matters most)

1. **[medium]** strength=1.000
   `event_backofflimitexceeded` — Job has reached the specified backoff limit
   *namespace: ccm-monitoring*
1. **[medium]** strength=1.000
   `event_backofflimitexceeded` — Job has reached the specified backoff limit
   *namespace: ccm-monitoring*
1. **[medium]** strength=1.000
   `event_backofflimitexceeded` — Job has reached the specified backoff limit
   *namespace: ccm-monitoring*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_volumeresizefailed` — mark PVC "cnv-images/tmp-pvc-8c15aced-31b5-4106-8b91-7a3307f12e8e" as resize finished fail
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*
1. **[medium]** strength=1.000
   `event_claimmisbound` — Two claims are bound to the same volume, this one is bound incorrectly
   *namespace: cnv-images*

### Fading Memories (weakest — about to be forgotten)

- str=0.100 `task_runner_on_failed` — Get a list of clusters on localhost
- str=0.100 `task_runner_on_failed` — Get a list of clusters on localhost
- str=0.100 `task_playbook_on_stats` —  on 
- str=0.100 `task_runner_on_failed` — Get vSphere login session on localhost
- str=0.100 `task_runner_on_failed` — Get a list of clusters on localhost
- str=0.100 `task_runner_on_failed` — Create OpenShift cluster using Assisted Installer on localhost
- str=0.100 `task_runner_on_failed` — Get a list of clusters on localhost
- str=0.100 `task_warning` — Set passthrough user data on 
- str=0.100 `task_warning` —  on 
- str=0.100 `task_warning` — Rewrite requirements, filter out installed collections (EE) on 

### Where the Memories Come From

- `default`: 232 (26.9%) █████████████
- `aap-general`: 124 (14.4%) ███████
- `cnv-images`: 103 (11.9%) █████
- `sandbox-884rh-ocp4-cluster`: 58 (6.7%) ███
- `sandbox-4wgls-ocp4-cluster`: 57 (6.6%) ███
- `sandbox-4zvww-ocp4-cluster`: 56 (6.5%) ███
- `sandbox-2nlhq-ocp4-cluster`: 51 (5.9%) ██
- `open-cluster-management`: 20 (2.3%) █
- `sandbox-6pm9r-ocp4-cluster`: 11 (1.3%) 
- `kubernetes-secret-generator`: 9 (1.0%) 
- *...and 58 more namespaces*

---

## What the Platform Learned

The cascade compression engine processed signals from 5 OpenShift clusters
and the Ansible Automation Platform over the course of this deployment.
Here is what it determined matters:

### Kubernetes Insights

- **event_deprecatedannotation** (583 memories, avg strength 0.566)
  - Service uses deprecated annotation metallb.universe.tf/ip-allocated-from-pool
  - Service uses deprecated annotation metallb.universe.tf/ip-allocated-from-pool
  - Service uses deprecated annotation metallb.universe.tf/ip-allocated-from-pool
- **event_volumefaileddelete** (162 memories, avg strength 0.961)
  - rpc error: code = Aborted desc = rbd csi-vol-143fc256-eeb4-4481-b5aa-43a6ed531d6
  - persistentvolume pvc-0b400beb-aa15-4d30-bfa6-925da8d6a429 is still attached to n
  - rpc error: code = Aborted desc = rbd csi-vol-0f24637f-1f80-4043-a99b-ee1021329a8
- **event_claimmisbound** (143 memories, avg strength 0.941)
  - Two claims are bound to the same volume, this one is bound incorrectly
  - Two claims are bound to the same volume, this one is bound incorrectly
  - Two claims are bound to the same volume, this one is bound incorrectly
- **event_failedtoretrieveimagepullsecret** (78 memories, avg strength 0.546)
  - Unable to retrieve some image pull secrets (search-pull-secret); attempting to p
  - Unable to retrieve some image pull secrets (search-pull-secret); attempting to p
  - Unable to retrieve some image pull secrets (search-pull-secret); attempting to p
- **event_ipaddresswrongreference** (65 memories, avg strength 0.902)
  - IPAddress: 172.30.1.13 for Service openshift-storage/rook-ceph-mgr has a wrong r
  - IPAddress: 172.30.1.69 for Service openshift-storage/rook-ceph-mgr has a wrong r
  - IPAddress: 172.30.10.20 for Service openshift-storage/rook-ceph-mgr has a wrong 

### Ansible Automation Platform Insights

- **task_runner_on_skipped** (4100 memories, avg strength 0.200)
  - Include secret_file if passed as extra-var on localhost
  - Include secret_file if passed as extra-var on localhost
  - Export in-memory inventory to inventory file on localhost
- **task_warning** (242 memories, avg strength 0.400)
  -  on 
  - Rewrite requirements, filter out installed collections (EE) on 
  -  on 
- **task_runner_on_failed** (74 memories, avg strength 0.700)
  - Get a list of clusters on localhost
  - Get vSphere login session on localhost
  - Get a list of clusters on localhost
- **task_verbose** (71 memories, avg strength 0.100)
  - Configure NetworkPolicy on 
  - Lock Bastion Security Group (EC2) on 
  - Lock Bastion Security Group (CNV) on 
- **task_runner_on_ok** (56 memories, avg strength 0.100)
  - Lock Bastion Security Group (CNV) on localhost
  - Configure NetworkPolicy on localhost
  - Lock Bastion Security Group (CNV) on localhost

---

*This document was generated automatically by the cascade compression engine.*
*Every memory listed above survived signal processing, content-hash deduplication,*
*and federated consolidation. What remains is the platform's institutional knowledge.*