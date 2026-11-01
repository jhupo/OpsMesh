"""Make the example's local tool package importable without loading its application."""

import sys
from pathlib import Path

_EXAMPLE_ROOT = Path(__file__).parents[2] / "examples/qq_community"
sys.path.insert(0, str(_EXAMPLE_ROOT))
