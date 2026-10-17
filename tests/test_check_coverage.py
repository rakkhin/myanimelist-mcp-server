import coverage
import pytest

from scripts.check_coverage import file_coverage, main


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project with fully, half and not measurable files, and its data."""

    def make(threshold: int = 95):
        (tmp_path / "pyproject.toml").write_text(
            f"[tool.coverage.report]\nfail_under = {threshold}\n"
        )
        files = {
            "full.py": ("a = 1\nb = 2\n", [1, 2]),
            "half.py": ("a = 1\nb = 2\nc = 3\nd = 4\n", [1, 2]),
            "empty.py": ("", []),
        }
        data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
        lines = {}
        for name, (source, covered) in files.items():
            (tmp_path / name).write_text(source)
            lines[str(tmp_path / name)] = covered
        data.add_lines(lines)
        data.write()
        monkeypatch.chdir(tmp_path)

    return make


def test_file_coverage_reports_threshold_and_each_file(project):
    project(threshold=95)

    threshold, percentages = file_coverage()

    assert threshold == 95
    assert percentages == {
        "empty.py": 100.0,
        "full.py": 100.0,
        "half.py": 50.0,
    }


def test_main_fails_and_names_files_below_threshold(
    project, monkeypatch, capsys
):
    project(threshold=95)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    assert main() == 1
    assert capsys.readouterr().out == (
        "half.py: 50.00% line coverage, below 95%\n"
    )


def test_main_annotates_failing_files_in_github_actions(
    project, monkeypatch, capsys
):
    project(threshold=95)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    main()

    assert (
        "::error file=half.py,title=Coverage below 95%::"
        "half.py: 50.00% line coverage, below 95%"
    ) in capsys.readouterr().out


def test_main_passes_when_every_file_meets_threshold(
    project, monkeypatch, capsys
):
    project(threshold=50)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    assert main() == 0
    assert capsys.readouterr().out == (
        "All 3 files at or above 50% coverage.\n"
    )
