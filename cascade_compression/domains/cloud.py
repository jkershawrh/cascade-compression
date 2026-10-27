"""Cloud infrastructure domain pack — OpenStack, IaaS control-plane log analysis."""

DOMAIN = "cloud"

SYSTEM_PROMPT = """You are classifying cloud infrastructure logs from an OpenStack deployment (compute, network, storage control plane). Signal sources include nova-api request logs, nova-compute hypervisor manager events, nova-scheduler host synchronization, neutron network operations, and image cache activity.

Context: a cloud control plane emits a high volume of routine traffic — successful API requests returning 200, periodic instance synchronization from hosts, and scheduled housekeeping. These are the healthy operating rhythm of the platform.

The noise/pattern line: successful API operations, periodic syncs, and housekeeping are noise. Repeated non-fatal warnings from a stable service are patterns. State divergence between the database and hypervisor, image cache problems, and repeated failures on the same resource need attention.

Classify as exactly one of:
- routine_noise: successful API requests, periodic host syncs, scheduled housekeeping
- known_pattern: repeated non-fatal warnings from a stable service, recurring request patterns
- needs_attention: database/hypervisor state divergence, image cache inconsistencies, repeated sync failures on one instance or host
- real_incident: instance loss on the hypervisor, node failure, API outage, control-plane failure requiring operator intervention

Answer with one word only."""

FEW_SHOT_EXEMPLARS = {
    "routine_noise": [
        '"GET /v2/54fadb412c4e40cdbaed9335e4c35a9e/servers/detail HTTP/1.1" status: 200 len: 1893 (successful API request)',
        "Successfully synced instances from host 'cp-1.slowvm1.tcloud-pg0.utah.cloudlab.us' (periodic scheduler sync)",
        "nova.osapi_compute.wsgi.server INFO request completed (routine control-plane traffic)",
    ],
    "known_pattern": [
        "nova.virt.libvirt.imagecache WARNING: Unknown base file: /var/lib/nova/instances/_base/ (recurring image cache housekeeping warning)",
        "Repeated 200 responses on servers/detail polling (stable tenant monitoring pattern)",
        "nova-compute INFO periodic task _sync_power_states running on schedule (recurring maintenance cycle)",
    ],
    "needs_attention": [
        "nova.compute.manager WARNING: While synchronizing instance power states, found 1 instances in the database and 0 instances on the hypervisor (state divergence)",
        "nova.compute.manager WARNING: found 1 instances in the database and 0 instances on the hypervisor recurring every 40 minutes (instance missing from hypervisor)",
        "nova.virt.libvirt.imagecache WARNING: Unknown base file repeated for the same instance (image cache inconsistency growing)",
    ],
    "real_incident": [
        "nova.compute.manager ERROR: Instance missing from hypervisor and unresolvable, marking instance ERROR (instance loss)",
        "nova-compute CRITICAL: connection to libvirt failed, compute node unusable (node failure)",
        "nova-api ERROR: unable to process requests, API returning 500s to all tenants (control-plane outage)",
    ],
}
