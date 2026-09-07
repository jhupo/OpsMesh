import json
from hashlib import sha256


def authorization_snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "fingerprint"}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{sha256(serialized.encode('utf-8')).hexdigest()}"
