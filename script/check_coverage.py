"""Enforce per-module coverage gates on a coverage.py JSON report.

The total ``fail_under`` value in ``pyproject.toml`` cannot detect a weakly
tested module hidden behind well tested ones. This gate implements the Home
Assistant quality-scale rules on a per-module basis:

* ``test-coverage``: above 95 % for every integration module.
* ``config-flow-test-coverage``: 100 % for ``config_flow.py``.

Coverage includes branch coverage (``[tool.coverage.run] branch = true``).
Every Python module of the integration must appear in the report, so a new
module cannot escape the gate by never being imported.

Usage::

    pytest --cov --cov-report=json:coverage.json
    python script/check_coverage.py coverage.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

INTEGRATION = Path("custom_components/opus_greennet")
# "Above 95 %" in the official rule: the module value must exceed this.
MODULE_THRESHOLD = 95.0
FULL_COVERAGE_MODULES = {"config_flow.py": 100.0}


def check(report_path: Path, root: Path = Path()) -> list[str]:
    """Return a list of gate violations for one coverage JSON report."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    files: dict[str, dict[str, dict[str, float]]] = report["files"]
    measured = {
        Path(name).name: data["summary"]["percent_covered"]
        for name, data in files.items()
        if Path(name).parent.as_posix().endswith(INTEGRATION.as_posix())
    }
    errors: list[str] = []
    expected = sorted(path.name for path in (root / INTEGRATION).glob("*.py"))
    for module in expected:
        if module not in measured:
            errors.append(f"{module}: missing from the coverage report")
            continue
        percent = measured[module]
        if (required := FULL_COVERAGE_MODULES.get(module)) is not None:
            if percent < required:
                errors.append(f"{module}: {percent:.2f} % < required {required:.0f} %")
        elif percent <= MODULE_THRESHOLD:
            errors.append(
                f"{module}: {percent:.2f} % is not above {MODULE_THRESHOLD:.0f} %"
            )
    return errors


def main(argv: list[str]) -> int:
    """Print the per-module result and return a process exit code."""
    report_path = Path(argv[1] if len(argv) > 1 else "coverage.json")
    errors = check(report_path)
    if errors:
        print("Per-module coverage gate failed:")
        for error in errors:
            print(f"  {error}")
        return 1
    print(
        f"Per-module coverage gate passed: every module above "
        f"{MODULE_THRESHOLD:.0f} %, config_flow.py at 100 %."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
