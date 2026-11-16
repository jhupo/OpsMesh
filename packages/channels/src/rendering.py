"""Explicit formatting helpers for caller-owned presentation policies."""


def table_text(data: dict) -> str:
    """Render every header and cell without interpreting markup."""
    lines = [data["caption"]] if data.get("caption") else []
    for index, row in enumerate(data["rows"], 1):
        lines.append(f"{index}.")
        for column, value in zip(data["columns"], row, strict=True):
            lines.append(f"{column}: {value if value is not None else 'null'}")
    if not data["rows"]:
        lines.append(" | ".join(data["columns"]))
    return "\n".join(lines)
