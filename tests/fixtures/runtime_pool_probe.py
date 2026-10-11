"""Linux acceptance of the real prefork/RPC boundary with an offline SDK transport."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


def start_service():
    import httpx
    from agents.models.openai_responses import OpenAIResponsesModel
    from openai import AsyncOpenAI
    from opsmesh_runtime.execution_control import initialize

    from opsmesh.agents.execution.providers.openai import runner
    from opsmesh.bootstrap.runtime_pool import serve

    preload_identity = uuid4().hex

    class Provider:
        def __init__(self, *, api_key, **kwargs):
            assert os.geteuid() >= 100000
            self.key = api_key

        def get_model(self, name):
            def respond(request):
                body = json.loads(request.content)
                assert request.url.path == "/v1/responses"
                assert not os.environ.get("PREVIOUS_TASK_CANARY")
                inputs = body["input"]
                if not any(item.get("type") == "function_call_output" for item in inputs):
                    output = [
                        {
                            "id": "call",
                            "type": "function_call",
                            "name": "probe",
                            "call_id": "probe-call",
                            "arguments": "{}",
                        }
                    ]
                else:
                    os.environ["PREVIOUS_TASK_CANARY"] = self.key
                    status = Path("/proc/self/status").read_text()
                    assert "CapEff:\t0000000000000000" in status
                    assert "NoNewPrivs:\t1" in status
                    answer = json.dumps(
                        {
                            "uid": os.getuid(),
                            "pid": os.getpid(),
                            "preload": preload_identity,
                            "history": len(inputs),
                        }
                    )
                    output = [
                        {
                            "id": "answer",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": answer, "annotations": []}],
                        }
                    ]
                response = {
                    "id": "offline",
                    "created_at": 0,
                    "model": name,
                    "object": "response",
                    "status": "completed",
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "output": output,
                }
                event = {"type": "response.completed", "sequence_number": 0, "response": response}
                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    text="data: " + json.dumps(event) + "\n\n",
                )

            return OpenAIResponsesModel(
                name,
                AsyncOpenAI(
                    api_key=self.key,
                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                ),
            )

    runner.OpenAIProvider = Provider
    original_run = runner.OpenAIAgentsRunner.run

    async def diagnostic_run(self, request):
        try:
            return await original_run(self, request)
        except Exception:
            import traceback

            Path(f"/tmp/opsmesh-runs/{os.getuid()}/fixture-error").write_text(
                traceback.format_exc()
            )
            raise

    runner.OpenAIAgentsRunner.run = diagnostic_run
    initialize()
    proxy = subprocess.Popen([sys.executable, "-m", "opsmesh_runtime.execution_control", "--proxy"])
    serve(proxy, capacity=2)


def exercise():
    from dataclasses import replace

    from opsmesh_runtime.execution_control import configure, revoke

    from opsmesh.agents.execution.contracts import (
        AgentRunRequest,
        AgentRuntimeApprovalDecision,
        AgentRuntimeContext,
        AgentRuntimeProfile,
        AgentRuntimeToolDefinition,
        AgentRuntimeToolResult,
        AgentRunTracing,
    )
    from opsmesh.runtime.agent_host.broker import RuntimeCallbackBroker
    from opsmesh.runtime.agent_host.wire import RESULT, RpcFrame, RuntimeRunInput
    from opsmesh.runtime.backends.docker import DockerSandboxSessionExecutor
    from opsmesh.runtime.contracts import SandboxBinding, SandboxManifest, SandboxSession
    from opsmesh.runtime.instances.execution_identity import RuntimeExecutionIdentity

    class Session:
        session_id = "acceptance"
        items = []

        async def get_items(self, limit=None):
            return self.items if limit is None else self.items[-limit:]

        async def add_items(self, items):
            self.items.extend(items)

        async def pop_item(self):
            return self.items.pop() if self.items else None

        async def clear_session(self):
            self.items.clear()

    class Tools:
        calls = 0

        async def authorize_agent(self, **kwargs):
            pass

        async def review_tool_call(self, **kwargs):
            return {"decision": "require_approval", "risk_level": "medium"}

        async def execute_tool(self, **kwargs):
            assert kwargs["approval_granted"]
            self.calls += 1
            return AgentRuntimeToolResult(
                status="completed",
                output={
                    "content": [{"type": "text", "text": "offline evidence"}],
                    "isError": False,
                },
            )

        async def cancel_active_tools(self, **kwargs):
            pass

    async def lifecycle(event):
        pass

    uid_base = 100000
    identities = [
        RuntimeExecutionIdentity(uuid4(), uid_base + i, {"mode": "none"}) for i in range(3)
    ]
    with open("/tmp/pool-probe.log", "wb") as log:
        supervisor = subprocess.Popen([sys.executable, __file__, "--serve"], stdout=log, stderr=log)
    clients = []
    try:
        deadline = time.monotonic() + 30
        while not Path("/run/opsmesh-agent-pool.sock").is_socket():
            assert supervisor.poll() is None, Path("/tmp/pool-probe.log").read_text()
            assert time.monotonic() < deadline
            time.sleep(0.05)
        for identity in identities:
            configure(identity.payload())
            # An execution directory must never supply privileged relay imports.
            directory = Path(f"/tmp/opsmesh-runs/{identity.uid}")
            (directory / "opsmesh_runtime.py").write_text(
                "raise AssertionError('Project module executed by privileged relay')\n"
            )

        def launch(identity, request=None):
            directory = f"/tmp/opsmesh-runs/{identity.uid}"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "opsmesh_runtime.agent_pool_client",
                    str(identity.allocation_id),
                    str(identity.uid),
                    directory,
                ],
                cwd=directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            clients.append(process)
            if request is not None:
                wire = RuntimeRunInput.from_request(request)
                process.stdin.write(
                    RpcFrame(type="start", payload=wire.model_dump(mode="json")).encoded()
                )
                process.stdin.flush()
            return process

        def run(identity, request):
            started = time.monotonic()
            process = launch(identity, request)
            broker = RuntimeCallbackBroker(request)
            events = []
            admission_ms = None
            while True:
                raw = process.stdout.readline()
                assert raw, "Runtime child disconnected"
                frame = RpcFrame.model_validate_json(raw)
                if frame.type == "ready":
                    admission_ms = round((time.monotonic() - started) * 1000)
                elif frame.type == "call":
                    payload = asyncio.run(broker.dispatch(frame.method, frame.payload))
                    process.stdin.write(
                        RpcFrame(type="reply", id=frame.id, payload=payload).encoded()
                    )
                    process.stdin.flush()
                elif frame.type == "event":
                    events.append(frame.payload)
                elif frame.type == "result":
                    result = RESULT.validate_python(frame.payload)
                    process.stdin.close()
                    assert process.wait(timeout=5) == 0
                    return result, admission_ms, events
                elif frame.type == "error":
                    detail = Path(f"/tmp/opsmesh-runs/{identity.uid}/fixture-error")
                    raise AssertionError(detail.read_text() if detail.exists() else frame.error)

        session, tools = Session(), Tools()
        workspace, run_id = uuid4(), uuid4()
        identity = identities[0]
        directory = f"/tmp/opsmesh-runs/{identity.uid}"
        request = AgentRunRequest(
            AgentRuntimeProfile(uuid4(), workspace, 1, "Offline", "expert", "Use probe", "offline"),
            "First question",
            AgentRuntimeContext(
                workspace,
                None,
                run_id,
                allowed_tools=("probe",),
                tool_definitions=(
                    AgentRuntimeToolDefinition(
                        "probe", "mcp", "Synthetic evidence", {"type": "object", "properties": {}}
                    ),
                ),
            ),
            api_key="pool-secret-canary",
            provider="openai",
            model="offline",
            model_api="responses",
            session=session,
            tool_executor=tools,
            tracing=AgentRunTracing("acceptance", disabled=True),
            stream=True,
            lifecycle_sink=lifecycle,
            sandbox=SandboxBinding(
                SandboxManifest(run_id, directory),
                SandboxSession(
                    "run",
                    directory,
                    "docker",
                    DockerSandboxSessionExecutor(
                        object(), "container", directory, 30, 10000, identity=identity
                    ),
                ),
            ),
        )
        paused, first_ms, events = run(identity, request)
        assert paused.resume_state and len(paused.interruptions) == 1 and tools.calls == 0
        interruption = paused.interruptions[0]
        resumed, second_ms, _ = run(
            identity,
            replace(
                request,
                resume_state=paused.resume_state,
                approval_decisions=(
                    AgentRuntimeApprovalDecision(
                        interruption.tool_call_id, interruption.tool_name, "approved"
                    ),
                ),
            ),
        )
        assert tools.calls == 1
        answer = json.loads(resumed.final_output)
        assert answer["uid"] == identity.uid
        followup, third_ms, _ = run(identity, replace(request, input_text="Follow-up"))
        next_answer = json.loads(followup.final_output)
        assert next_answer["history"] > answer["history"]
        assert next_answer["preload"] == answer["preload"] and next_answer["pid"] != answer["pid"]

        # Hold both real execution children before their first frozen frame.
        held = [launch(item) for item in identities[:2]]
        time.sleep(0.3)
        rejected = launch(identities[2])
        assert (
            json.loads(rejected.stdout.readline())["error"]["code"] == "sdk_pool_admission_failed"
        )
        rejected.stdin.close()
        rejected.wait(timeout=5)
        revoke(identities[0].payload())
        assert held[0].wait(timeout=5) == 0
        assert held[1].poll() is None and supervisor.poll() is None
        held[1].stdin.close()
        held[1].wait(timeout=5)
        # Reject forged/stale allocations and direct admission by an execution UID.
        forged = RuntimeExecutionIdentity(uuid4(), identities[1].uid, {"mode": "none"})
        bad = launch(forged)
        assert json.loads(bad.stdout.readline())["error"]["message"] == "PermissionError"
        bad.stdin.close()
        bad.wait(timeout=5)
        unauthorized = subprocess.run(
            [
                sys.executable,
                "-c",
                "import socket; s=socket.socket(socket.AF_UNIX); "
                "s.connect('/run/opsmesh-agent-pool.sock')",
            ],
            user=identities[1].uid,
            capture_output=True,
        )
        assert unauthorized.returncode != 0
        report = {
            "native_sdk_approval_resume_session": "passed",
            "fresh_identity_state": "passed",
            "capacity_cancel_neighbour": "passed",
            "forged_admission": "rejected",
            "preload_reused": True,
            "admission_ms": [first_ms, second_ms, third_ms],
            "external_calls": 0,
        }
        print(json.dumps(report), flush=True)
    finally:
        for process in clients:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        for identity in identities:
            revoke(identity.payload())
        supervisor.terminate()
        supervisor.wait(timeout=10)
        assert "pool-secret-canary" not in Path("/tmp/pool-probe.log").read_text()


if __name__ == "__main__":
    if sys.argv[1:] == ["--serve"]:
        start_service()
    else:
        exercise()
