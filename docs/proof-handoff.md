# Production evidence and the OSS release

Reviewed 2026-10-20. Production-proof work can improve the public engine, but an observation from
another implementation does not automatically qualify this release. Evidence must identify the
exact tested package, image, code, contracts, configuration, models, taxonomy, and observation window.

## Two separate transfers

**Reusable engineering:** a private finding becomes an environment-independent fix, a public
contract change where needed, and a synthetic regression test. It enters OSS through normal
review, compatibility checks, tests, and the public-boundary workflow.

**Public evidence:** a reviewed aggregate may support a bounded claim only when artifact identity,
independent truth, denominators, runtime, interruptions, failures, and limitations are complete.
Raw evidence and operational configuration remain private. The public register records the
approved claim class; related implementation results are labeled as context rather than release proof.

## Exact artifact rule

1. Freeze the OSS candidate and create its attested package/image and candidate manifest.
2. Evaluate that exact artifact. A proof runner must not silently import another local engine.
3. Complete independent review before model evaluation; preserve the frozen corpus digest and
   exact model/taxonomy/configuration revisions. Separate label-coverage and representative tests.
4. For staging readiness, obtain a successful `cascade-stage-evidence` result and complete the
   attested handoff described in [staging-evidence.md](staging-evidence.md).
5. Publish only approved, sanitized evidence and promote the exact tested artifacts through
   [RELEASING.md](../RELEASING.md). A later change requires fresh qualification.

Public mechanics, staging qualification, production suitability, and customer value are distinct.
The incubating release profile allows publication of tested mechanics without effectiveness
claims. Staging success supports its declared bounded run; it does not establish customer savings
or universal production safety. A value claim additionally needs the actual matched baseline,
complete operating costs, and customer validation.

## Authority remains in Cascade

Models supply classification or verification evidence. Cascade owns admission, fallback,
promotion, expiry, demotion, memory verification, and policy authority. Similarity, score margins,
classifier agreement, and strong memories cannot independently approve a benign outcome.
Custom decision models are optional research until their contract, licensing, calibration, and
independent held-out evidence are reviewed. No custom CDM is introduced by this documentation.

See [evidence-register.md](evidence-register.md), [roadmap.md](roadmap.md), and
[adopter-release.md](adopter-release.md).
