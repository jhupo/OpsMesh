import hashlib


def memory_content_fingerprint(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\x00{content}".encode()).hexdigest()
