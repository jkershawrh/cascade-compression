from pathlib import Path
import re
import cascade_compression


ROOT = Path(__file__).resolve().parents[1]


def test_package_and_runtime_versions_match():
    project = (ROOT / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
    assert declared is not None
    assert declared.group(1) == cascade_compression.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", cascade_compression.__version__)


def test_first_release_policy_files_exist():
    required = {
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "GOVERNANCE.md",
        "LICENSE",
        "RELEASING.md",
        "SECURITY.md",
        "SUPPORT.md",
    }
    assert all((ROOT / name).is_file() for name in required)
