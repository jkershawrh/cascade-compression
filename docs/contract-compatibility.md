# Public contract compatibility

`contracts/manifest.json` is the source of truth for public Cascade wire contracts. Each contract
has an independent semantic version, stability level, and JSON Schema. Source and installed
packages expose the same artifacts through `cascade_compression.contracts`.

## Compatibility rules

- Stable contracts may add optional fields within a major version. Consumers must ignore fields
  they do not understand.
- Removing a field, changing its meaning or type, tightening a previously valid constraint, or
  changing an enum incompatibly requires a new major version.
- Alpha contracts may change before promotion to stable. Producers must preserve and publish the
  exact alpha version used for an artifact.
- A breaking release requires migration notes and compatibility fixtures for the previous major
  version.
- Specialized collectors advertise capabilities through the `cascade.collector-plugin` descriptor;
  environment configuration and credentials are never part of that descriptor.

Collector packages register implementations through the
`cascade_compression.collectors` Python entry-point group. The entry-point name must match the
collector descriptor name. The loader rejects duplicate names, incompatible API majors,
unsupported capabilities, and implementations that do not inherit `BaseCollector`.

Domain packs register modules through the `cascade_compression.domains` entry-point group. The
entry-point name must match the module's `DOMAIN` value; prompts and memory configuration are loaded
through that contract instead of package-path imports.

## Contract ownership

The OSS engine owns these contracts. The production-proof layer validates released contracts but
does not redefine them. Private deployment and evidence systems consume the same versions and keep
their environment-specific extensions outside public payloads.
