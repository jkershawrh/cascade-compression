#!/usr/bin/env python3
"""Fail when tracked OSS files contain private deployment material.

Gitleaks remains the credential scanner. This check enforces Cascade-specific repository
boundaries and scans exactly what Git would publish, avoiding generated virtual environments.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath


PRIVATE_DIRECTORIES = {"deploy", "private", "local-evidence"}
PRIVATE_FILENAMES = {
    ".env",
    "id_ed25519",
    "id_rsa",
    "kubeconfig",
}
PRIVATE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}


def _literal(*parts: str) -> re.Pattern[str]:
    return re.compile(re.escape("".join(parts)), re.IGNORECASE)


CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private cluster alias", _literal("infra", "01")),
    ("private location alias", _literal("dal", "12")),
    ("private API namespace", _literal("api", ".ocpv")),
    ("private DNS suffix", _literal(".infra", ".demo", ".redhat", ".com")),
    ("private host alias", _literal("obe", "ron")),
    ("private service alias", _literal("baby", "lon")),
    ("private service alias", _literal("pool", "boy")),
    ("private organization alias", _literal("rhp", "ds")),
    ("private service alias", _literal("star", "gate")),
    ("private sandbox alias", _literal("sandbox", ".conan")),
    ("private service alias", _literal("laba", "gator")),
    ("OpenShift bearer token", re.compile("sha256" + r"~[A-Za-z0-9_-]{20,}")),
    ("OpenShift API endpoint", re.compile(r"https?://api\.[A-Za-z0-9.-]+:6443", re.I)),
    ("macOS home path", re.compile("/" + r"Users/[^/\s]+/")),
    ("Linux home path", re.compile("/" + r"home/[^/\s]+/")),
    ("Windows home path", re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\", re.I)),
)


def path_violations(relative_path: str) -> list[str]:
    path = PurePosixPath(relative_path)
    lowered_parts = {part.lower() for part in path.parts}
    violations = []
    if lowered_parts & PRIVATE_DIRECTORIES:
        violations.append("private directory")
    name = path.name.lower()
    if name in PRIVATE_FILENAMES or name.startswith(".env.") or name.startswith("kubeconfig"):
        violations.append("private filename")
    if path.suffix.lower() in PRIVATE_SUFFIXES:
        violations.append("private key/certificate suffix")
    return violations


def content_violations(payload: bytes) -> list[str]:
    if b"\0" in payload:
        return []
    text = payload.decode("utf-8", errors="replace")
    return [label for label, pattern in CONTENT_PATTERNS if pattern.search(text)]


def tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def check_repository(root: Path) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for relative in tracked_paths(root):
        for reason in path_violations(relative):
            findings.append((relative, reason))
        path = root / relative
        if not path.is_file():
            continue
        for reason in content_violations(path.read_bytes()):
            findings.append((relative, reason))
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = check_repository(root)
    if findings:
        for path, reason in findings:
            print(f"public-boundary violation: {path}: {reason}")
        return 1
    print("Public boundary: tracked files contain no blocked private material")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
