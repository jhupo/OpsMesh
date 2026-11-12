"""Public errors for execution lifecycle and tracing configuration."""


class ExecutionError(Exception):
    """Base error for execution contract violations."""


class ExecutionClosed(ExecutionError):
    """The execution plugin is not accepting work."""


class TracingConfigurationError(ExecutionError):
    """The configured tracing destination cannot be initialized."""
