__all__ = ["RunOrchestrationService"]

def __getattr__(name: str):
    if name in {"RunOrchestrationService", "build_default_queue"}:
        from .service import RunOrchestrationService, build_default_queue

        return {"RunOrchestrationService": RunOrchestrationService, "build_default_queue": build_default_queue}[name]
    raise AttributeError(name)
