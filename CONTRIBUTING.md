# Contributing

Contributions must be environment-independent, covered by tests, and compatible with the public
contracts. Use synthetic fixtures only. Do not submit production identities, endpoints, manifests,
memories, replay output, work logs, economics, or credentials.

Before opening a pull request:

```bash
uv sync --extra dev --locked
uv run pytest -q
uv build
```

Breaking contract changes require a new major contract version, migration notes, and compatibility
fixtures for the previous major version.

By contributing, you agree that your contribution is licensed under Apache-2.0. Contributions must
be your own work or include clear attribution and a compatible license.
