"""PyInstaller entry point; the executable also launches isolated worker processes."""

from opsmesh.bootstrap.cli import entrypoint

if __name__ == "__main__":
    entrypoint()
