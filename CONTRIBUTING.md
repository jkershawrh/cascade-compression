# Contributing

Contributions must be environment-independent, covered by tests, and compatible with the public
contracts. Use synthetic fixtures only. Do not submit production identities, endpoints, manifests,
memories, replay output, work logs, economics, or credentials.

Before opening a pull request:

```bash
pytest -q
python -m build
```

Breaking contract changes require a new major contract version, migration notes, and compatibility
fixtures for the previous major version.
