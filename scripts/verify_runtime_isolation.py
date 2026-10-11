"""Opt-in Linux Docker acceptance; uses synthetic SDK tools, never business APIs.

Run with an immutable OpsMesh Runtime image. The temporary host is removed even
on failure; its workload limits and capabilities match production supervision.
"""

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

NETWORK_PROBE = r"""
import json,os,socket,sys,httpx
mode=sys.argv[1]
result={'uid':os.getuid()}
status=open('/proc/self/status').read().splitlines()
assert next(line for line in status if line.startswith('CapEff:')).split()[1]=='0000000000000000'
assert next(line for line in status if line.startswith('NoNewPrivs:')).split()[1]=='1'
try:os.setuid(0)
except PermissionError:pass
else:raise AssertionError('Workload elevated its identity')
for host,port in [('127.0.0.1',20001),('172.17.0.1',8000)]:
 try:socket.create_connection((host,port),timeout=.5).close()
 except OSError:pass
 else:raise AssertionError('Workload bypassed its network identity')
proxy='http://127.0.0.1:20000' if mode=='restricted' else None
try:
 response=httpx.get('https://example.org',proxy=proxy,trust_env=False,timeout=15)
 assert mode!='none' and response.status_code==200
except httpx.TransportError:
 assert mode=='none'
if mode=='restricted':
 try:httpx.get('https://example.com',proxy=proxy,trust_env=False,timeout=5)
 except httpx.TransportError:pass
 else:raise AssertionError('Proxy accepted a destination outside the allowlist')
print(json.dumps({'network':mode,'verified':True}))
"""

SDK_PROBE = r"""
import asyncio,json,os
from agents import Agent,Runner,RunState,SQLiteSession,function_tool,set_tracing_disabled
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall,ResponseOutputMessage,ResponseOutputText
set_tracing_disabled(True)
calls=[]
seen=[]
class Offline(Model):
 async def get_response(self,*,input,**kwargs):
  seen.append(input)
  if any(isinstance(item,dict) and item.get('type')=='function_call_output' for item in input):
   output=[ResponseOutputMessage(id='answer',type='message',role='assistant',status='completed',
    content=[ResponseOutputText(type='output_text',text='verified',annotations=[])])]
  else:
   output=[ResponseFunctionToolCall(type='function_call',name='inspect_logs',call_id='verify-call',arguments='{}')]
  return ModelResponse(output=output,usage=Usage(),response_id='offline')
 async def stream_response(self,*args,**kwargs):
  raise AssertionError('Unexpected stream')
  yield
@function_tool(needs_approval=True)
async def inspect_logs()->str:
 calls.append('once')
 await asyncio.sleep(.1)
 return 'synthetic log'
async def main():
 agent=Agent(name='acceptance',model=Offline(),tools=[inspect_logs])
 session=SQLiteSession('verification',f'/tmp/opsmesh-runs/{os.getuid()}/sdk.db')
 interrupted=await Runner.run(agent,'first-turn',session=session)
 assert len(interrupted.interruptions)==1 and not calls
 state=await RunState.from_string(agent,interrupted.to_state().to_string())
 state.approve(interrupted.interruptions[0])
 resumed=await Runner.run(agent,state,session=session)
 assert resumed.final_output=='verified' and calls==['once']
 followup=await Runner.run(agent,'followup',session=session)
 assert followup.final_output=='verified' and calls==['once']
 assert 'first-turn' in json.dumps(seen[-1]) and 'synthetic log' in json.dumps(seen[-1])
 print(json.dumps({'sdk_approval_resume_session':True,'uid':os.getuid()}))
asyncio.run(main())
"""

MCP_PROBE = r"""
import asyncio,json,os,sys
from pathlib import Path
from agents.mcp import MCPServerStdio
root=Path('/tmp/opsmesh-runs')/str(os.getuid())
server=root/'server.py'
server.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("acceptance")\n'
 '@m.tool()\ndef inspect_logs()->str:return "synthetic-log"\n'
 'if __name__=="__main__":m.run(transport="stdio")\n')
async def main():
 async with MCPServerStdio(params={'command':sys.executable,'args':[str(server)]}) as session:
  tools=await session.list_tools()
  assert [tool.name for tool in tools]==['inspect_logs']
  result=await session.call_tool('inspect_logs',{})
  assert not result.isError and 'synthetic-log' in result.content[0].text
 print(json.dumps({'native_mcp_stdio':True,'uid':os.getuid()}))
asyncio.run(main())
"""

LONG_PROCESS = r"""
import os,subprocess,time
from pathlib import Path
root=Path('/tmp/opsmesh-runs')/str(os.getuid())
subprocess.Popen(['python','-c','import os,time;os.setsid();time.sleep(300)'])
(root/'started').write_text('ready')
while True:
 (root/'tick').write_text(str(time.monotonic()))
 time.sleep(.05)
"""


class DockerProbe:
    def __init__(self, image: str) -> None:
        self.image = image
        self.name = f"opsmesh-verify-{uuid4().hex[:12]}"

    def command(self, args: list[str], *, data: bytes | None = None, check: bool = True) -> bytes:
        completed = subprocess.run(
            ["docker", *args], input=data, capture_output=True, timeout=90, check=False
        )
        if check and completed.returncode:
            raise RuntimeError(completed.stderr.decode(errors="replace")[-2000:])
        return completed.stdout

    def execute(self, uid: int, code: str, *args: str) -> bytes:
        return self.command(
            ["exec", "--user", f"{uid}:{uid}", self.name, "python", "-c", code, *args]
        )

    def control(self, action: str, identity: dict) -> None:
        self.command(
            [
                "exec",
                "-i",
                "--user",
                "0:0",
                self.name,
                "python",
                "-m",
                "opsmesh_runtime.execution_control",
            ],
            data=json.dumps({"action": action, "execution": identity}).encode(),
        )

    def start(self) -> None:
        self.command(
            [
                "run",
                "-d",
                "--name",
                self.name,
                "--init",
                "--user",
                "0:0",
                "--read-only",
                "--network",
                "bridge",
                "--cpus",
                "1",
                "--memory",
                "512m",
                "--pids-limit",
                "128",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "NET_ADMIN",
                "--cap-add",
                "KILL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "DAC_OVERRIDE",
                "--cap-add",
                "SETUID",
                "--cap-add",
                "SETGID",
                "--security-opt",
                "no-new-privileges:true",
                "--security-opt",
                "apparmor=docker-default",
                "--tmpfs",
                "/run:rw,noexec,nosuid,nodev,size=32m",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m",
                "--tmpfs",
                "/workspace:rw,nosuid,nodev,size=64m",
                self.image,
                "python",
                "-m",
                "opsmesh_runtime.execution_control",
                "--initialize",
            ]
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ready = self.command(
                [
                    "exec",
                    "--user",
                    "0:0",
                    self.name,
                    "python",
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('/run/squid/squid.pid').exists(); print('ready')",
                ],
                check=False,
            )
            if ready.strip() == b"ready":
                return
            time.sleep(0.1)
        raise RuntimeError("Runtime supervisor did not start")

    def verify(self) -> None:
        identities = [
            {
                "allocation_id": str(uuid4()),
                "uid": 100000,
                "network_policy": {
                    "mode": "restricted",
                    "allowed_domains": ["example.org"],
                    "allowed_ports": [443],
                    "allowed_protocols": ["tcp"],
                },
            },
            {"allocation_id": str(uuid4()), "uid": 100001, "network_policy": {"mode": "none"}},
            {"allocation_id": str(uuid4()), "uid": 100002, "network_policy": {"mode": "internet"}},
        ]
        for identity in identities:
            self.control("configure", identity)
            print(
                self.execute(identity["uid"], NETWORK_PROBE, identity["network_policy"]["mode"])
                .decode()
                .strip(),
                flush=True,
            )
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda uid: self.execute(uid, SDK_PROBE), [100000, 100001]))
        for outcome in outcomes:
            print(outcome.decode().strip(), flush=True)
        print(self.execute(100001, MCP_PROBE).decode().strip(), flush=True)
        self.verify_files(identities)
        for uid in (100001, 100002):
            self.command(
                ["exec", "-d", "--user", f"{uid}:{uid}", self.name, "python", "-c", LONG_PROCESS]
            )
        for uid in (100001, 100002):
            self.execute(
                uid,
                "from pathlib import Path; import time,sys; p=Path(sys.argv[1]); "
                "[(time.sleep(.05)) for _ in range(40) if not p.exists()]; "
                "assert p.exists()",
                f"/tmp/opsmesh-runs/{uid}/started",
            )
        self.execute(
            100001,
            "from pathlib import Path; p=Path('/tmp/opsmesh-runs/100002/tick'); "
            "\ntry:p.read_bytes()\nexcept PermissionError:pass\nelse:"
            "raise AssertionError('Private files leaked')",
        )
        before = self.execute(100001, "print(open('/tmp/opsmesh-runs/100001/tick').read())")
        self.control("revoke", identities[2])
        self.execute(
            0,
            "from pathlib import Path; assert not "
            "Path('/tmp/opsmesh-runs/100002').exists(); "
            "assert not any(p.name.isdecimal() and p.stat().st_uid==100002 "
            "for p in Path('/proc').iterdir() if p.exists())",
        )
        after = self.execute(100001, "print(open('/tmp/opsmesh-runs/100001/tick').read())")
        assert before != after, "Cancelling one identity stopped its neighbour"
        self.control("revoke", identities[2])  # Retry is idempotent.
        identities[2] = {
            "allocation_id": str(uuid4()),
            "uid": 100002,
            "network_policy": {"mode": "none"},
        }
        self.control("configure", identities[2])
        self.execute(
            100002,
            "from pathlib import Path; assert not "
            "Path('/tmp/opsmesh-runs/100002/started').exists()",
        )
        print(
            json.dumps(
                {
                    "cancel_escaped_children": True,
                    "neighbour_running": True,
                    "identity_reuse_clean": True,
                }
            ),
            flush=True,
        )
        for identity in identities:
            self.control("revoke", identity)

    def verify_files(self, identities: list[dict]) -> None:
        import io
        import tarfile
        from pathlib import PurePosixPath
        from uuid import UUID

        from opsmesh.runtime.backends.docker import (
            DockerSandboxSessionExecutor,
            DockerSdkRuntimeClient,
        )
        from opsmesh.runtime.instances.execution_identity import RuntimeExecutionIdentity

        docker = DockerSdkRuntimeClient(lambda: 30)
        identity = RuntimeExecutionIdentity(
            UUID(identities[0]["allocation_id"]), 100000, identities[0]["network_policy"]
        )
        self.execute(0, "import os; os.mkdir('/workspace/input',0o700)")
        bundle = io.BytesIO()
        with tarfile.open(fileobj=bundle, mode="w") as archive:
            member = tarfile.TarInfo("staged.txt")
            member.size, member.mode = 6, 0o600
            archive.addfile(member, io.BytesIO(b"staged"))
        docker.copy_archive_to_container(self.name, "/workspace/input", bundle.getvalue(), 30)
        self.execute(
            0,
            "import os; os.chown('/workspace/input',100000,100000); "
            "os.chown('/workspace/input/staged.txt',100000,100000)",
        )
        executor = DockerSandboxSessionExecutor(
            docker, self.name, "/workspace/input", 30, 128, identity=identity
        )
        sealed = docker.exec_command(
            self.name,
            ["chmod", "700", "/workspace/input"],
            30,
            working_dir="/",
            identity=identity,
        )
        assert sealed.exit_code == 0, "Run owner could not seal its workspace"
        assert executor.read_file(PurePosixPath("/workspace/input/staged.txt")) == b"staged"
        executor.write_file(PurePosixPath("/workspace/input/new.txt"), io.BytesIO(b"written"))
        assert executor.read_file(PurePosixPath("/workspace/input/new.txt")) == b"written"
        self.execute(
            100001,
            "from pathlib import Path; "
            "Path('/tmp/opsmesh-runs/100001/private').write_text('private')",
        )
        self.execute(
            100000,
            "import os; os.symlink('/tmp/opsmesh-runs/100001/private','/workspace/input/link')",
        )
        try:
            executor.read_file(PurePosixPath("/workspace/input/link"))
        except ValueError:
            pass
        else:
            raise AssertionError("Sandbox file read bypassed execution ownership")
        malicious = io.BytesIO()
        with tarfile.open(fileobj=malicious, mode="w") as archive:
            member = tarfile.TarInfo("link/overwrite")
            member.size = 0
            archive.addfile(member, io.BytesIO())
        try:
            docker.copy_archive_to_container(
                self.name, "/workspace/input", malicious.getvalue(), 30
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Archive followed a link outside its run scope")
        print(
            json.dumps(
                {
                    "archive_on_readonly_rootfs": True,
                    "identity_file_io": True,
                    "cross_identity_symlink_denied": True,
                }
            ),
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Immutable OpsMesh Runtime image digest")
    args = parser.parse_args()
    if not args.image.startswith("sha256:") and "@sha256:" not in args.image:
        parser.error("Use an immutable image digest")
    probe = DockerProbe(args.image)
    try:
        probe.start()
        probe.verify()
    finally:
        probe.command(["rm", "-f", probe.name], check=False)


if __name__ == "__main__":
    main()
