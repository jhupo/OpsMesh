from uuid import UUID


def bounded_text(value: str, max_length: int, default: str) -> str:
    text = value.strip()
    if not text:
        text = default
    return text[:max_length]


def bounded_optional(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text[:max_length] if text else None


def normalized_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        text = tag.strip().lower()[:64]
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
        if len(normalized) >= 20:
            break
    return normalized


def optional_uuid_from_metadata(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
