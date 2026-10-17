"""Fail if any measured file is below the coverage threshold.

coverage.py's fail_under only checks the total. This applies the same
threshold, [tool.coverage.report] fail_under, to every measured file:

    uv run pytest --cov=.
    uv run python scripts/check_coverage.py
"""

from __future__ import annotations

import os
import sys

import coverage


def file_coverage(
    data_file: str = ".coverage", config_file: str = "pyproject.toml"
) -> tuple[float, dict[str, float]]:
    """Return the threshold and each measured file's line coverage in %."""
    cov = coverage.Coverage(data_file=data_file, config_file=config_file)
    cov.load()
    threshold = float(cov.get_option("report:fail_under"))
    percentages = {}
    for path in sorted(cov.get_data().measured_files()):
        _, statements, _, missing, _ = cov.analysis2(path)
        covered = len(statements) - len(missing)
        percent = 100.0 * covered / len(statements) if statements else 100.0
        percentages[os.path.relpath(path)] = percent
    return threshold, percentages


def main() -> int:
    threshold, percentages = file_coverage()
    below = {p: pct for p, pct in percentages.items() if pct < threshold}
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for path, percent in below.items():
        message = f"{path}: {percent:.2f}% line coverage, below {threshold:g}%"
        print(message)
        if in_actions:
            print(
                f"::error file={path},title=Coverage below {threshold:g}%::{message}"
            )
    if below:
        return 1
    print(f"All {len(percentages)} files at or above {threshold:g}% coverage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
