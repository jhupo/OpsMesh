"""Start the bundled executable without treating it as a package module."""

from opsmesh_service.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
