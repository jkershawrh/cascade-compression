# Adopt and release an incubation candidate

The supported stable release is v0.1.0. Current source is 0.2.0rc7; this is not a published
0.2 stable release. A source change after a candidate build invalidates that candidate for
the changed source. Run the documented first example against the exact wheel/image you distribute.

1. Merge reviewed source and freeze its commit. Run the Staging candidate workflow on that commit.
2. Verify its candidate manifest, package hashes, SBOM and attestations with the commands in
   [RELEASING.md](../RELEASING.md). Test the downloaded wheel outside any source checkout.
3. Install that exact downloaded wheel: `python -m pip install /absolute/path/to/verified.whl`.
   Start the verified image by immutable GHCR digest, not a moving main/RC tag.
4. Repeat the bundled first-run replay and service health check. `/health.package_version` must
   match the wheel version. OpenAPI `info.version` is the API contract version, not the package version.
5. After review, tag that same commit and use the existing release workflow; verify the GitHub
   release contains the corresponding packages/manifests and immutable image identity. Never
   create a GitHub release manually to bypass missing candidate evidence.

No new release is published by this checklist or PR. PyPI publishing remains disabled pending
a project-owned trusted publisher; it is not required to distribute verified wheels and images.

For a shared service, put authentication/access policy and TLS in front of the API and document
storage, backup and limits. The local loopback example is not a multi-tenant hosting profile.
Independent consumers should exercise install, process, restart/state restoration and upgrade
from 0.1 before claiming compatibility. Classifier effectiveness, staging readiness and customer
savings remain separate from an incubating OSS release.
