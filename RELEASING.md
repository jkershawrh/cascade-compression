# Release policy

## Release gate

1. Complete independent review and adjudication of the frozen holdout. Generate a same-corpus
   classification report; model agreement alone is not release evidence.
2. Update `CHANGELOG.md`, `pyproject.toml`, and `cascade_compression.__version__` to the same
   prerelease Semantic Versioning value, then freeze that candidate commit.
3. Run the manual **Staging candidate** workflow on that exact commit. It reruns tests and package
   smoke checks, then publishes a multi-architecture `candidate-<commit>` image with SBOM and
   provenance. Retain its candidate manifest and immutable image digest; embed that unmodified
   manifest in the staging manifest so `cascade-stage-evidence` can bind the tested run to it.
4. Deploy that digest—not a mutable tag—to the isolated staging allocation. Run the classification,
   runtime, audit-delivery, capacity, and clean-clone checks over one declared window. Assemble them
   with `cascade-stage-evidence`; do not proceed unless it returns `staging_success` with no failed
   gates.
5. Confirm CI, public safety, and credential scans pass and review the complete diff plus sanitized
   evidence for internal names, endpoints, deployment details, and raw records.
6. Create an annotated tag named `v<version>` on the exact tested candidate commit and push it. The release
   workflow signs the resulting package and container provenance through GitHub artifact
   attestations. Versions containing `a`, `b`, or `rc` are published as GitHub prereleases and must
   not move stable major/minor container tags.
7. Attach the sanitized staging evidence and its digest to the prerelease. Keep blinded records,
   review receipts, disagreement queues, and raw runtime/audit exports private.

The tag workflow builds the wheel, source archive, SPDX SBOM, and multi-architecture container. It
publishes the container to `ghcr.io/jkershawrh/cascade-compression`, records build provenance, signs
GitHub artifact attestations, and creates a GitHub release. PyPI publishing is intentionally disabled
until a project-owned trusted publisher is configured.

Never tag an RC merely because unit tests are green. A candidate without a complete independently
adjudicated corpus, immutable runtime evidence, and healthy audit delivery remains an engineering
snapshot.

## Verification

With the GitHub CLI installed, verify a downloaded artifact:

```bash
gh attestation verify cascade_compression-0.2.0rc1-py3-none-any.whl \
  --repo jkershawrh/cascade-compression
```

Container provenance and SBOM attestations are attached to the GHCR image manifest and can be
inspected with tooling that supports OCI attestations.
