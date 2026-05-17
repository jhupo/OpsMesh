from pathlib import Path


class LocalStorage:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def write(self, storage_key: str, content: bytes) -> None:
        path = self._path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def read(self, storage_key: str) -> bytes:
        return self._path_for_key(storage_key).read_bytes()

    def exists(self, storage_key: str) -> bool:
        return self._path_for_key(storage_key).exists()

    def _path_for_key(self, storage_key: str) -> Path:
        path = (self._root / storage_key).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("Storage key escapes storage root")
        return path

