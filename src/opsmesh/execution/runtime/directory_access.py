"""Initialize workspace ACLs through the authorized native Docker session."""

from agents.sandbox.session import SandboxSession

from opsmesh.execution.runtime.helpers.source import helper_source
from opsmesh.execution.runtime.schemas import SANDBOX_WORKDIR
from opsmesh.shared.errors import ConflictError


async def grant_directory_access(native: SandboxSession, uid: int, service_group: int) -> None:
    if uid < 0 or service_group < 0:
        raise ValueError("The sandbox UID and service GID must be nonnegative")
    result = await native.exec(
        "python3",
        "-I",
        "-c",
        helper_source("grant_access"),
        SANDBOX_WORKDIR,
        str(uid),
        str(service_group),
        user="0:0",
        shell=False,
        timeout=60,
    )
    if result.exit_code:
        raise ConflictError(
            "Workspace ACL initialization failed; the compute image requires Python 3 and acl, "
            "and its mounted filesystem must support POSIX ACLs"
        )
