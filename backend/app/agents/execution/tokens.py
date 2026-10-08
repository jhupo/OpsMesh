def estimate_token_upper_bound(value: str) -> int:
    """Return a conservative tokenizer-independent upper bound for UTF-8 model input."""

    return len(value.encode("utf-8"))


def truncate_to_token_bound(value: str, token_bound: int) -> str:
    if token_bound <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= token_bound:
        return value
    suffix = "\n[context truncated]"
    suffix_bytes = suffix.encode("utf-8")
    if token_bound <= len(suffix_bytes):
        return encoded[:token_bound].decode("utf-8", errors="ignore")
    prefix = encoded[: token_bound - len(suffix_bytes)].decode("utf-8", errors="ignore").rstrip()
    return prefix + suffix
