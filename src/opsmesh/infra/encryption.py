import json
from typing import Any

from cryptography.fernet import Fernet


class SecretCipher:
    def __init__(self, key: str) -> None:
        self.fernet = Fernet(key.encode())

    def encrypt(self, value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
        return self.fernet.encrypt(raw).decode()

    def decrypt(self, value: str) -> Any:
        return json.loads(self.fernet.decrypt(value.encode()))
