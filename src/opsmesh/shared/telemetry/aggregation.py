from __future__ import annotations


def top_counts(counts: dict[str, int], limit: int) -> list[dict[str, object]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
