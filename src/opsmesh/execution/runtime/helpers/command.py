"""Set the borrowed workspace environment before replacing this container process."""

import json
import os
import sys


def main() -> None:
    os.chdir(sys.argv[1])
    os.environ.update(json.loads(sys.argv[2]))
    os.execvp(sys.argv[3], sys.argv[3:])


if __name__ == "__main__":
    main()
