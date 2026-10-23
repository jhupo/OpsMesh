"""Load packaged helper source without interpolating executable user input."""

from functools import cache
from importlib.resources import files
from typing import Literal


@cache
def helper_source(name: Literal["read_file", "write_file", "grant_access", "command"]) -> str:
    return files(__package__).joinpath(f"{name}.py").read_text(encoding="utf-8")
