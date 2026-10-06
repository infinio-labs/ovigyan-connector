import re
from pathlib import Path

from ovigyan_connector import __version__


def test_the_version_in_the_code_matches_pyproject():
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', text, re.M).group(1) == __version__


def test_a_real_version_is_reported():
    assert __version__ != "0.0.0"
