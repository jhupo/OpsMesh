"""Verify local Markdown links inside a source archive without extracting it."""

import argparse
import posixpath
import re
import tarfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    args = parser.parse_args()
    archive = args.archive
    if archive is None:
        archives = list((Path(__file__).resolve().parents[1] / "dist").glob("*.tar.gz"))
        if len(archives) != 1:
            parser.error("specify an archive, or keep exactly one source archive in dist/")
        archive = archives[0]
    errors: list[str] = []
    with tarfile.open(archive) as bundle:
        names = set(bundle.getnames())
        for member in bundle.getmembers():
            if not member.isfile() or not member.name.endswith(".md"):
                continue
            stream = bundle.extractfile(member)
            if stream is None:
                continue
            with stream:
                text = stream.read().decode("utf-8")
            prose = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
            for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", prose):
                target = match[1].strip().strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path or target.startswith("//"):
                    continue
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(member.name), unquote(parsed.path))
                )
                if resolved not in names and not any(n.startswith(resolved + "/") for n in names):
                    errors.append(f"{member.name}: missing source archive link {target}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Source archive Markdown links passed: {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
