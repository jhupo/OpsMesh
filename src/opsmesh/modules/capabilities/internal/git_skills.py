"""Import pinned Git blobs without checkout filters, hooks, submodules or repository code."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.capabilities.api import GitSkillSource


class GitSkillImporter:
    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...] = (),
        local_roots: tuple[Path, ...] = (),
        max_files: int = 1000,
        max_bytes: int = 20_000_000,
        timeout_seconds: float = 60,
    ) -> None:
        self.allowed_hosts = {host.casefold() for host in allowed_hosts}
        self.local_roots = tuple(root.resolve() for root in local_roots)
        self.max_files, self.max_bytes, self.timeout = max_files, max_bytes, timeout_seconds

    def _repository(self, value: str) -> tuple[str, bool]:
        url = urlsplit(value)
        if url.scheme == "https":
            if (
                not url.hostname
                or url.hostname.casefold() not in self.allowed_hosts
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise OpsMeshError(
                    "AUTHORIZATION_DENIED",
                    "Git host is not allowlisted or URL contains credentials",
                )
            return value, False
        path = Path(value)
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not path.is_dir()
            or not any(path.resolve().is_relative_to(root) for root in self.local_roots)
        ):
            raise OpsMeshError(
                "AUTHORIZATION_DENIED",
                "Git import requires allowlisted HTTPS or trusted local repository",
            )
        return str(path.resolve()), True

    async def _git(self, directory: Path, *arguments: str, local: bool = False) -> bytes:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        environment.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_LFS_SKIP_SMUDGE="1",
        )
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "credential.helper=",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=" + ("always" if local else "never"),
            "-c",
            "http.followRedirects=false",
            *arguments,
            cwd=directory,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise OpsMeshError(
                "GIT_IMPORT_FAILED", "Git could not read the pinned repository commit"
            )
        if len(stdout) > self.max_bytes:
            raise OpsMeshError("RESOURCE_LIMIT", "Git output exceeds the import limit")
        return stdout

    def _entries(self, listing: bytes, subdirectory: str) -> list[tuple[str, str, int]]:
        prefix = "" if subdirectory == "." else subdirectory.rstrip("/") + "/"
        entries = []
        paths: set[str] = set()
        total = 0
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            path = raw_path.decode("utf-8")
            if not path.startswith(prefix):
                continue
            mode, kind, digest, raw_size = metadata.decode().split()
            relative = path.removeprefix(prefix)
            parts = PurePosixPath(relative).parts
            if (
                mode not in {"100644", "100755"}
                or kind != "blob"
                or not parts
                or any(part.casefold() in {".", "..", ".git"} for part in parts)
                or "\\" in relative
                or ":" in relative
                or relative.startswith("/")
            ):
                raise OpsMeshError(
                    "PATH_INVALID", "Git Skill contains a symlink, submodule or unsafe path"
                )
            if relative.casefold() in paths:
                raise OpsMeshError("PATH_INVALID", "Git Skill has case-colliding asset paths")
            paths.add(relative.casefold())
            size = int(raw_size)
            total += size
            entries.append((relative, digest, size))
            if len(entries) > self.max_files or total > self.max_bytes:
                raise OpsMeshError("RESOURCE_LIMIT", "Git Skill exceeds its file or byte limit")
        if not any(path == "SKILL.md" for path, _, _ in entries):
            raise OpsMeshError("CONFIG_INVALID", "Git Skill directory requires SKILL.md")
        return entries

    @asynccontextmanager
    async def import_source(self, source: GitSkillSource) -> AsyncIterator[Path]:
        repository, local = self._repository(source.repository)
        if source.subdirectory != "." and (
            not re.fullmatch(r"[A-Za-z0-9._/-]+", source.subdirectory)
            or any(part in {"", ".", "..", ".git"} for part in source.subdirectory.split("/"))
        ):
            raise OpsMeshError(
                "PATH_INVALID", "Git Skill subdirectory must be a safe relative path"
            )
        with TemporaryDirectory(prefix="opsmesh-git-skill-") as temporary:
            root = Path(temporary)
            await self._git(root, "init", "--bare", "repo")
            repo = root / "repo"
            await self._git(
                repo,
                "fetch",
                "--depth=1",
                "--no-tags",
                "--no-recurse-submodules",
                "--",
                repository,
                source.commit,
                local=local,
            )
            actual = (await self._git(repo, "rev-parse", "FETCH_HEAD^{commit}")).decode().strip()
            if actual.casefold() != source.commit.casefold():
                raise OpsMeshError("CONTENT_CORRUPT", "Git fetch did not resolve the pinned commit")
            listing = await self._git(repo, "ls-tree", "-r", "-l", "-z", actual)
            entries = self._entries(listing, source.subdirectory)
            destination = root / "skill"
            destination.mkdir()
            for relative, digest, size in entries:
                content = await self._git(repo, "cat-file", "blob", digest)
                if len(content) != size:
                    raise OpsMeshError(
                        "CONTENT_CORRUPT", "Git blob size differs from its tree entry"
                    )
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(target.write_bytes, content)
            yield destination
