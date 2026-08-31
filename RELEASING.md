# Release policy

## Release gate

1. Update `CHANGELOG.md`, `pyproject.toml`, and `cascade_compression.__version__` to the same
   Semantic Versioning value.
2. Run `uv lock`, `uv run pytest -q`, `uv build`, and `scripts/release_smoke.py` against an isolated
   wheel installation.
3. Confirm public safety and credential scans pass and review the complete diff for internal names,
   endpoints, deployment details, and raw evidence.
4. Merge to `main` and wait for all required checks.
5. Create an annotated tag named `v<version>` on the reviewed commit and push the tag. The release
   workflow signs the resulting package and container provenance through GitHub artifact
   attestations.

The tag workflow builds the wheel, source archive, SPDX SBOM, and multi-architecture container. It
publishes the container to `ghcr.io/jkershawrh/cascade-compression`, records build provenance, signs
GitHub artifact attestations, and creates a GitHub release. PyPI publishing is intentionally disabled
until a project-owned trusted publisher is configured.

## Verification

With the GitHub CLI installed, verify a downloaded artifact:

```bash
gh attestation verify cascade_compression-0.1.0-py3-none-any.whl \
  --repo jkershawrh/cascade-compression
```

Container provenance and SBOM attestations are attached to the GHCR image manifest and can be
inspected with tooling that supports OCI attestations.
