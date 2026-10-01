"""Keep published component identifiers independent of Python package layout."""

from typing import Any

from fastapi import FastAPI

# These seven identifiers were published before feature colocation. They are wire
# contract names, not Python import paths. Keep them stable for generated clients.
_PUBLISHED_NAMES = {
    'backend__app__runtime__workers__schemas__WorkerHeartbeatRequest': (
        'backend__app__api__schemas__operations__workers__WorkerHeartbeatRequest'
    ),
    'backend__app__runtime__workers__schemas__WorkerHeartbeatResponse': (
        'backend__app__api__schemas__operations__workers__WorkerHeartbeatResponse'
    ),
    'backend__app__runtime__self_hosted__schemas__WorkerHeartbeatRequest': (
        'backend__app__api__schemas__operations__self_hosted__WorkerHeartbeatRequest'
    ),
    'backend__app__runtime__self_hosted__schemas__WorkerHeartbeatResponse': (
        'backend__app__api__schemas__operations__self_hosted__WorkerHeartbeatResponse'
    ),
    'backend__app__runtime__instances__schemas__RuntimeEventResponse': (
        'backend__app__api__schemas__operations__runtimes__RuntimeEventResponse'
    ),
    'backend__app__shared__http__pagination__PageResponse_RuntimeEventResponse___1': (
        'backend__app__api__pagination__PageResponse_RuntimeEventResponse___1'
    ),
    'backend__app__shared__http__pagination__PageResponse_RuntimeEventResponse___2': (
        'backend__app__api__pagination__PageResponse_RuntimeEventResponse___2'
    ),
}


def preserve_published_schema_names(app: FastAPI) -> None:
    generate = app.openapi
    references = {
        f"#/components/schemas/{current}": f"#/components/schemas/{published}"
        for current, published in _PUBLISHED_NAMES.items()
    }

    def rewrite_references(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref" and isinstance(item, str) and item in references:
                    value[key] = references[item]
                else:
                    rewrite_references(item)
        elif isinstance(value, list):
            for item in value:
                rewrite_references(item)

    def openapi() -> dict[str, Any]:
        document = generate()
        schemas = document.get("components", {}).get("schemas", {})
        for current, published in _PUBLISHED_NAMES.items():
            if current in schemas:
                if published in schemas:
                    raise ValueError(f"Duplicate published OpenAPI component: {published}")
                schemas[published] = schemas.pop(current)
        rewrite_references(document)
        return document

    app.openapi = openapi  # type: ignore[method-assign]
