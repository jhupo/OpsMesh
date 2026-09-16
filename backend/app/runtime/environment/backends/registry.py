from backend.app.runtime.environment.backends.contracts import (
    RuntimeBackend,
    RuntimeBackendCapabilities,
)


class RuntimeBackendRegistry:
    def __init__(self, backends: dict[str, RuntimeBackend]) -> None:
        if not backends:
            raise ValueError("Runtime backend registry must not be empty")
        if any(not name.strip() for name in backends):
            raise ValueError("Runtime backend names must not be empty")
        self._backends = dict(backends)

    def resolve(self, provider: str) -> RuntimeBackend | None:
        return self._backends.get(provider)

    def require(self, provider: str) -> RuntimeBackend:
        backend = self.resolve(provider)
        if backend is None:
            raise LookupError(f"Runtime provider {provider!r} is not registered")
        return backend

    def capabilities(self) -> dict[str, RuntimeBackendCapabilities]:
        return {key: backend.capabilities for key, backend in self._backends.items()}
