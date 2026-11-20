"""Tracing destination installation and local diagnostics tests."""

import asyncio
import builtins
import json
from types import SimpleNamespace

import pytest
from agents import RunConfig
from agents.tracing import custom_span, get_current_trace

import opsmesh.plugins.execution._tracing.support as tracing_module
from opsmesh.plugins.execution._tracing import TraceContext, TracingSupport
from opsmesh.plugins.execution._tracing.langfuse import LangfuseBridge
from opsmesh.plugins.execution._tracing.local import (
    LocalTraceProcessor,
)
from opsmesh.plugins.execution.config import (
    LangfuseSettings,
    TracingSettings,
)
from opsmesh.plugins.execution.errors import TracingConfigurationError


def test_disabled_tracing_clears_processors(monkeypatch):
    processors = []
    disabled = []
    monkeypatch.setattr(
        tracing_module,
        "set_trace_processors",
        lambda value: processors.append(value),
    )
    monkeypatch.setattr(
        tracing_module,
        "set_tracing_disabled",
        disabled.append,
    )
    support = TracingSupport(TracingSettings())
    support.install()
    assert support.installed
    assert support.snapshot() is None
    assert processors == [[]]
    assert disabled == [True]


@pytest.mark.anyio
async def test_disabled_tracing_shutdown_is_a_noop():
    support = TracingSupport(TracingSettings())
    await support.shutdown()
    assert not support.installed


def test_local_processor_summarizes_without_content():
    processor = LocalTraceProcessor(capacity=2)
    processor.on_trace_start(SimpleNamespace(trace_id="t1"))
    processor.on_span_start(SimpleNamespace(span_id="s1"))
    processor.on_span_end(
        SimpleNamespace(
            trace_id="t1",
            span_id="s1",
            span_data=SimpleNamespace(),
            error=None,
        )
    )
    processor.on_span_end(
        SimpleNamespace(
            trace_id="t1",
            span_id="s2",
            span_data=SimpleNamespace(),
            error=SimpleNamespace(message="secret"),
        )
    )
    processor.on_trace_end(SimpleNamespace(trace_id="t1"))
    snapshot = processor.snapshot()
    assert snapshot["traces_started"] == 1
    assert snapshot["traces_finished"] == 1
    assert snapshot["spans_finished"] == 2
    assert snapshot["errors"] == 1
    assert len(snapshot["recent_spans"]) == 2
    assert "secret" not in str(snapshot)


def test_local_destination_replaces_existing_processors(monkeypatch):
    installed = []
    disabled = []
    monkeypatch.setattr(
        tracing_module, "set_trace_processors", installed.append
    )
    monkeypatch.setattr(tracing_module, "set_tracing_disabled", disabled.append)
    support = TracingSupport(TracingSettings(destination="local"))
    support.install()
    assert support.installed
    assert len(installed) == 1
    assert len(installed[0]) == 1
    assert isinstance(installed[0][0], LocalTraceProcessor)
    assert disabled == [False]
    assert support.snapshot() is not None


@pytest.mark.anyio
async def test_local_shutdown_clears_exporters(monkeypatch):
    installed = []
    disabled = []
    monkeypatch.setattr(
        tracing_module, "set_trace_processors", installed.append
    )
    monkeypatch.setattr(tracing_module, "set_tracing_disabled", disabled.append)
    support = TracingSupport(TracingSettings(destination="local"))
    support.install()
    await support.shutdown()
    assert not support.installed
    assert installed[-1] == []
    assert disabled[-1] is True
    assert support.snapshot() is not None


def test_langfuse_requires_environment_secrets(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    support = TracingSupport(
        TracingSettings(
            destination="langfuse",
            langfuse=LangfuseSettings(
                base_url="https://langfuse.example.com",
                public_key_ref="LANGFUSE_PUBLIC_KEY",
                secret_key_ref="LANGFUSE_SECRET_KEY",
            ),
        )
    )
    with pytest.raises(TracingConfigurationError, match="LANGFUSE_PUBLIC_KEY"):
        support.install()
    assert not support.installed


def test_langfuse_requires_optional_packages(monkeypatch):
    original_import = builtins.__import__

    def import_without_openinference(name, *args, **kwargs):
        if name == "openinference.instrumentation":
            raise ImportError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_openinference)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    support = TracingSupport(
        TracingSettings(
            destination="langfuse",
            langfuse=LangfuseSettings(
                base_url="https://langfuse.example.com",
                public_key_ref="LANGFUSE_PUBLIC_KEY",
                secret_key_ref="LANGFUSE_SECRET_KEY",
            ),
        )
    )
    with pytest.raises(
        TracingConfigurationError, match="Langfuse|OpenInference"
    ):
        support.install()
    assert not support.installed


@pytest.mark.parametrize("host_allowed", [False, True])
@pytest.mark.parametrize("run_allowed", [False, True])
def test_prepare_sensitive_data_is_a_host_ceiling(host_allowed, run_allowed):
    support = TracingSupport(
        TracingSettings(
            destination="local", include_sensitive_data=host_allowed
        )
    )
    original = RunConfig(
        workflow_name="configured", trace_include_sensitive_data=run_allowed
    )

    prepared = support.prepare(original)

    assert prepared is not original
    assert original.trace_include_sensitive_data is run_allowed
    assert prepared.trace_include_sensitive_data is (
        host_allowed and run_allowed
    )
    assert prepared.workflow_name == "configured"


def test_prepare_preserves_sandbox_run_configuration():
    from agents.run_config import SandboxRunConfig

    support = TracingSupport(TracingSettings(destination="local"))
    original = RunConfig(workflow_name="sandbox", sandbox=SandboxRunConfig())

    prepared = support.prepare(original)

    assert prepared.sandbox is original.sandbox
    assert prepared.trace_include_sensitive_data is False


@pytest.mark.anyio
async def test_scope_opens_native_trace_before_execution_and_reuses_outer():
    support = TracingSupport(TracingSettings(destination="local"))
    support.install()
    try:
        with support.scope(
            support.prepare(None), TraceContext(session_id="s")
        ) as trace_id:
            assert get_current_trace().trace_id == trace_id
            with support.scope(support.prepare(None)) as nested:
                assert nested == trace_id
            assert get_current_trace().trace_id == trace_id
        assert get_current_trace() is None
        assert support.snapshot()["traces_started"] == 1
        assert support.snapshot()["traces_finished"] == 1
    finally:
        await support.shutdown()


@pytest.mark.anyio
async def test_disabled_run_never_opens_a_trace():
    support = TracingSupport(TracingSettings(destination="local"))
    support.install()
    try:
        with support.scope(
            support.prepare(RunConfig(tracing_disabled=True))
        ) as trace_id:
            assert trace_id is None
            assert get_current_trace() is None
        assert support.snapshot()["traces_started"] == 0
    finally:
        await support.shutdown()


@pytest.fixture
def langfuse_export(monkeypatch):
    pytest.importorskip("langfuse")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exports = []

    def create_provider(bridge, public_key, secret_key):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        exports.append(exporter)
        return provider

    monkeypatch.setattr(LangfuseBridge, "_create_provider", create_provider)
    monkeypatch.setenv("TEST_TRACE_PUBLIC", "public")
    monkeypatch.setenv("TEST_TRACE_SECRET", "secret")
    return exports


def langfuse_settings():
    return TracingSettings(
        destination="langfuse",
        langfuse=LangfuseSettings(
            base_url="http://langfuse.invalid",
            public_key_ref="TEST_TRACE_PUBLIC",
            secret_key_ref="TEST_TRACE_SECRET",
            environment="test",
        ),
    )


@pytest.mark.anyio
async def test_langfuse_native_spans_include_user_session_and_sdk_trace(
    langfuse_export,
):
    support = TracingSupport(langfuse_settings())
    support.install()
    try:
        with support.scope(
            support.prepare(None),
            TraceContext(
                user_id="user-1", session_id="session-1", tags=("chat",)
            ),
            "example.chat",
        ) as sdk_trace_id:
            with custom_span("tool"):
                pass
        spans = langfuse_export[0].get_finished_spans()
        assert len(spans) == 2
        for span in spans:
            assert span.attributes["user.id"] == "user-1"
            assert span.attributes["session.id"] == "session-1"
            assert span.attributes["langfuse.trace.tags"] == ("chat",)
            assert (
                json.loads(
                    span.attributes["langfuse.trace.metadata.sdk_trace_id"]
                )
                == sdk_trace_id
            )
            assert (
                json.loads(span.attributes["langfuse.trace.metadata.plugin_id"])
                == "example.chat"
            )
            assert span.attributes["langfuse.environment"] == "test"
        assert len({span.context.trace_id for span in spans}) == 1
        assert spans[0].context.trace_id != int(
            sdk_trace_id.removeprefix("trace_"), 16
        )
    finally:
        await support.shutdown()


@pytest.mark.anyio
async def test_langfuse_restart_uses_new_provider_and_leaves_global_provider(
    langfuse_export,
):
    from opentelemetry.trace import get_tracer_provider

    original_provider = get_tracer_provider()
    for index in range(2):
        support = TracingSupport(langfuse_settings())
        support.install()
        try:
            with support.scope(support.prepare(None)):
                with custom_span(f"call-{index}"):
                    pass
        finally:
            await support.shutdown()
    assert len(langfuse_export) == 2
    assert len(langfuse_export[0].get_finished_spans()) == 2
    assert len(langfuse_export[1].get_finished_spans()) == 2
    assert get_tracer_provider() is original_provider


@pytest.mark.anyio
async def test_existing_instrumentor_is_not_owned_or_closed(langfuse_export):
    first = TracingSupport(langfuse_settings())
    second = TracingSupport(langfuse_settings())
    first.install()
    try:
        with pytest.raises(TracingConfigurationError, match="already owned"):
            second.install()
        assert not second.installed
        with first.scope(first.prepare(None)):
            with custom_span("still-running"):
                pass
        assert len(langfuse_export) == 1
        assert len(langfuse_export[0].get_finished_spans()) == 2
    finally:
        await first.shutdown()


@pytest.mark.anyio
async def test_trace_context_does_not_leak_into_next_run(langfuse_export):
    support = TracingSupport(langfuse_settings())
    support.install()
    try:
        with support.scope(
            support.prepare(None), TraceContext(user_id="first")
        ):
            pass
        with support.scope(support.prepare(None)):
            pass
        spans = langfuse_export[0].get_finished_spans()
        assert spans[0].attributes["user.id"] == "first"
        assert "user.id" not in spans[1].attributes
    finally:
        await support.shutdown()


@pytest.mark.anyio
async def test_parallel_trace_contexts_are_isolated(langfuse_export):
    support = TracingSupport(langfuse_settings())
    support.install()
    entered = asyncio.Event()

    async def observe(user_id):
        with support.scope(
            support.prepare(RunConfig(group_id=f"session-{user_id}")),
            TraceContext(user_id=user_id),
        ):
            entered.set()
            await asyncio.sleep(0)
            with custom_span(f"tool-{user_id}"):
                await entered.wait()

    try:
        await asyncio.gather(observe("one"), observe("two"))
        spans = langfuse_export[0].get_finished_spans()
        assert len(spans) == 4
        grouped = {}
        for span in spans:
            grouped.setdefault(span.context.trace_id, set()).add(
                (span.attributes["user.id"], span.attributes["session.id"])
            )
        assert {frozenset(values) for values in grouped.values()} == {
            frozenset({("one", "session-one")}),
            frozenset({("two", "session-two")}),
        }
    finally:
        await support.shutdown()


def test_failed_instrumentation_closes_owned_provider(monkeypatch):
    pytest.importorskip("langfuse")
    from openinference.instrumentation.openai_agents import (
        OpenAIAgentsInstrumentor,
    )

    closed = []
    provider = SimpleNamespace(
        add_span_processor=lambda processor: None,
        shutdown=lambda: closed.append(True),
    )

    def fail_install(self, **kwargs):
        raise RuntimeError("instrumentation failed")

    monkeypatch.setenv("TEST_TRACE_PUBLIC", "public")
    monkeypatch.setenv("TEST_TRACE_SECRET", "secret")
    monkeypatch.setattr(
        LangfuseBridge, "_create_provider", lambda *args: provider
    )
    monkeypatch.setattr(OpenAIAgentsInstrumentor, "instrument", fail_install)
    support = TracingSupport(langfuse_settings())

    with pytest.raises(TracingConfigurationError, match="instrumentation"):
        support.install()

    assert closed == [True]
    assert not support.installed
    assert not OpenAIAgentsInstrumentor().is_instrumented_by_opentelemetry


def test_partial_instrumentation_restores_previous_sdk_processor(
    monkeypatch, langfuse_export
):
    from agents.tracing import set_trace_processors, trace
    from openinference.instrumentation.openai_agents import (
        OpenAIAgentsInstrumentor,
    )

    previous = LocalTraceProcessor()
    set_trace_processors([previous])
    original_install = OpenAIAgentsInstrumentor._instrument

    def fail_after_patching(self, **kwargs):
        original_install(self, **kwargs)
        raise RuntimeError("failed after installing SDK processor")

    monkeypatch.setattr(
        OpenAIAgentsInstrumentor, "_instrument", fail_after_patching
    )
    support = TracingSupport(langfuse_settings())
    try:
        with pytest.raises(TracingConfigurationError, match="instrumentation"):
            support.install()
        tracing_module.set_tracing_disabled(False)
        with trace("still-owned-by-previous"):
            with custom_span("still-visible"):
                pass
        assert previous.snapshot()["traces_started"] == 1
        assert previous.snapshot()["spans_started"] == 1
        assert not OpenAIAgentsInstrumentor().is_instrumented_by_opentelemetry
    finally:
        set_trace_processors([])
        tracing_module.set_tracing_disabled(True)


def approval_agent():
    from agents import Agent, function_tool
    from agents.testing import ScriptedModel
    from agents.testing.model import ModelStep, assistant_message, function_call

    resumed_trace_ids = []

    @function_tool(needs_approval=True)
    def approved_action() -> str:
        """Perform the approved test action."""
        resumed_trace_ids.append(get_current_trace().trace_id)
        return "approved"

    agent = Agent(
        name="approval-test",
        tools=[approved_action],
        model=ScriptedModel(
            [
                ModelStep(
                    output=[
                        function_call("approved_action", {}, call_id="approval")
                    ]
                ),
                ModelStep(output=[assistant_message("done")]),
            ]
        ),
    )
    return agent, resumed_trace_ids


@pytest.mark.anyio
async def test_approval_resume_keeps_sdk_trace_identity():
    from agents import Runner

    agent, resumed_trace_ids = approval_agent()
    support = TracingSupport(TracingSettings(destination="local"))
    support.install()
    config = support.prepare(None)
    try:
        with support.scope(config) as original_trace_id:
            interrupted = await Runner.run(
                agent, "perform action", run_config=config
            )
        state = interrupted.to_state()
        state.approve(interrupted.interruptions[0])

        with support.scope(config, resume=True) as before_trace_id:
            assert before_trace_id is None
            assert get_current_trace() is None
            result = await Runner.run(agent, state, run_config=config)

        assert result.final_output == "done"
        assert resumed_trace_ids == [original_trace_id]
        assert get_current_trace() is None
        assert support.snapshot()["traces_started"] == 1
    finally:
        await support.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("new_trace", [False, True])
async def test_langfuse_approval_resume_trace_policy(
    langfuse_export, new_trace
):
    from agents import Runner, gen_trace_id

    agent, observed_trace_ids = approval_agent()
    support = TracingSupport(langfuse_settings())
    support.install()
    config = support.prepare(RunConfig(group_id="approval-session"))
    try:
        with support.scope(config) as original_trace_id:
            interrupted = await Runner.run(agent, "act", run_config=config)
        state = interrupted.to_state()
        state.approve(interrupted.interruptions[0])
        previous_spans = len(langfuse_export[0].get_finished_spans())
        resumed_id = gen_trace_id() if new_trace else None
        config = support.prepare(
            RunConfig(trace_id=resumed_id, group_id="approval-session")
        )

        with support.scope(config, resume=True):
            result = await Runner.run(agent, state, run_config=config)

        spans = langfuse_export[0].get_finished_spans()
        assert result.final_output == "done"
        assert observed_trace_ids == [resumed_id or original_trace_id]
        if new_trace:
            assert len(spans) > previous_spans
            resumed_spans = spans[previous_spans:]
            for span in resumed_spans:
                assert span.attributes["session.id"] == "approval-session"
                assert (
                    json.loads(
                        span.attributes["langfuse.trace.metadata.sdk_trace_id"]
                    )
                    == resumed_id
                )
        else:
            # OpenInference 2.8.0 drops spans after a native ReattachedTrace.
            assert len(spans) == previous_spans
    finally:
        await support.shutdown()
