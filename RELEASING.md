# Release policy

## Release levels

`config/release-profile.json` declares the claim level for the exact release commit:

- `incubating_oss` publishes experimental software and mechanics without classification
  effectiveness, natural compression-rate, staging-readiness, or production claims. It is allowed
  only for a prerelease version.
- `staging_qualified` additionally requires the complete, attested `staging_success` handoff.

Both levels require clean source, tests, package/container smoke checks, immutable candidate
artifacts, SBOM, provenance, and public/private boundary checks. The difference is the claim being
made, not the quality of the build.

## Incubating OSS prerelease

1. Set `claim_level` to `incubating_oss`, keep `staging_evidence_required` false, and state the
   experimental limitations in `config/release-profile.json`.
2. Update `CHANGELOG.md`, `pyproject.toml`, and `cascade_compression.__version__` to the same
   prerelease Semantic Versioning value, then freeze that candidate commit.
3. Run the manual **Staging candidate** workflow on that exact commit. It reruns tests and package
   smoke checks, generates and attests the wheel, source archive, package SBOM, and multi-architecture
   candidate image with container SBOM and provenance.
4. Confirm CI and public safety pass. Review the complete diff and release profile for internal
   names, endpoints, deployment details, raw records, or claims beyond incubation.
5. Create and push the annotated `v<version>` tag on that exact candidate commit. The release
   workflow independently verifies the candidate manifest, package hashes, image provenance,
   prerelease version, and incubation profile before publishing. The GitHub release is visibly
   titled **incubating OSS** and includes the release profile.

An incubation release intentionally has no `staging-evidence.json`. It must never move stable
major/minor container tags.

## Staging-qualified release

1. Complete independent review and adjudication of the frozen `label_coverage_challenge` holdout.
   Generate a same-corpus classification report; model agreement alone is not release evidence.
   Keep a separately frozen `representative_prevalence` report for natural label-mix, compression,
   or workload-rate claims. Never infer prevalence from the challenge corpus.
2. Set `claim_level` to `staging_qualified`, set `staging_evidence_required` true, update the package
   and runtime versions, and freeze the candidate commit.
3. Run the manual **Staging candidate** workflow on that exact commit.
4. Deploy that digest—not a mutable tag—to an isolated staging allocation. Run classification,
   runtime, audit-delivery, capacity, and clean-clone checks over one declared window. Assemble them
   with `cascade-stage-evidence`; do not proceed unless it returns `staging_success` with no failed
   gates.
5. Submit the sanitized result to the manual **Staging evidence** workflow for the exact candidate
   commit. It verifies and attests the complete handoff. For example:

   ```bash
   evidence_base64=$(base64 < staging-evidence.json | tr -d '\n')
   gh workflow run staging-evidence.yml \
     --ref main \
     -f candidate_commit=FULL_GIT_SHA \
     -f evidence_base64="$evidence_base64"
   ```

6. Confirm CI, public safety, credential scans, and the sanitized evidence are clean.
7. Create and push the annotated `v<version>` tag on the exact tested candidate commit. The release
   workflow verifies the staging handoff in addition to every incubation artifact, promotes the
   exact image digest without rebuilding it, and attaches the sanitized staging evidence.
8. Keep blinded records, review receipts, disagreement queues, and raw runtime/audit exports private.

The candidate workflow builds and attests the wheel, source archive, SPDX SBOM, and
multi-architecture container. The tag workflow repeats tests and a clean package build as a
reproducibility check, but publishes the already attested candidate package artifacts and retags
the already tested GHCR manifest digest. The aggregate manifest records the claim level, exact
package hashes, and immutable container digest. Staging-qualified releases also record the verified
staging evidence chain.
PyPI publishing is intentionally disabled until a project-owned trusted publisher is configured.

The container base is pinned to a multi-architecture manifest digest, and
`requirements-container.lock` and `requirements-build.lock` are hash-locked exports of the
production and build dependencies in `uv.lock`. The production export deliberately includes the
`semantic-classifier` extra: the ordinary Python package keeps llm-d-sc support optional, while the
official candidate and release images guarantee that the gRPC client is installed and importable.
A multi-stage build keeps build tooling out of the runtime image. When dependencies change,
regenerate both locks with the commands encoded in CI; candidate and release workflows fail if
either export is stale.

```bash
uv export --frozen --no-dev --extra semantic-classifier --no-emit-project --no-header \
  --format requirements-txt --output-file requirements-container.lock
uv export --frozen --only-group build --no-emit-project --no-header \
  --format requirements-txt --output-file requirements-build.lock
```

Never describe an incubation release as staging-ready or production-ready merely because its tests
are green. Human-adjudicated classification, immutable runtime evidence, and healthy audit delivery
remain mandatory for the separate `staging_qualified` claim.

## Verification

With the GitHub CLI installed, verify a downloaded artifact:

```bash
gh attestation verify cascade_compression-0.2.0rc4-py3-none-any.whl \
  --repo jkershawrh/cascade-compression
```

Container provenance and SBOM attestations are attached to the GHCR image manifest and can be
inspected with tooling that supports OCI attestations.
