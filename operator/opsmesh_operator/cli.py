from __future__ import annotations

import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(prog="opsmesh")
    parser.add_argument("command", choices=["version"])
    parser.parse_args()
    print(version("opsmesh-operator"))


if __name__ == "__main__":
    main()
