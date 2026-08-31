# Governance

The project uses a maintainer-led model. Maintainers review changes for contract compatibility,
safety invariants, licensing, test coverage, and the public/private data boundary.

Routine changes require one approving maintainer and passing required checks. Changes to stable
contracts, promotion safety, release automation, or the security boundary require an explicit
maintainer approval and migration notes where applicable. Releases are created from protected tags
after the release checklist passes.

Project decisions and technical disagreements should be recorded in a GitHub issue or pull request
so the rationale remains public. Maintainers may revert changes that violate safety guarantees or
introduce private operational material.
