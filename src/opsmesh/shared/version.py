"""Read the installed distribution version through Python package metadata."""

from importlib.metadata import version

_DISTRIBUTION_NAME = "opsmesh"


def package_version() -> str:
    return version(_DISTRIBUTION_NAME)
