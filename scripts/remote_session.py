"""Run operator-authorized SSH operations; credentials arrive only on stdin."""

import base64
import getpass
import json

import paramiko


def main() -> None:
    credentials = json.loads(getpass.getpass("SSH credentials: "))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        credentials["host"],
        username=credentials["username"],
        password=credentials["password"],
        timeout=15,
    )
    print("SSH connected", flush=True)
    secrets = [credentials["password"]]
    try:
        while True:
            action = json.loads(getpass.getpass("Operation: "))
            if action["kind"] == "close":
                break
            if action["kind"] == "upload":
                with client.open_sftp() as sftp:
                    sftp.put(action["local"], action["remote"])
                print("Uploaded " + action["remote"], flush=True)
            elif action["kind"] == "file":
                content = base64.b64decode(action["content"])
                secrets.extend(action.get("redact", []))
                with client.open_sftp() as sftp:
                    with sftp.open(action["path"], "wb") as output:
                        output.write(content)
                    sftp.chmod(action["path"], 0o600)
                print("Configured protected remote file", flush=True)
            elif action["kind"] == "run":
                _, stdout, _ = client.exec_command(action["command"], get_pty=True)
                for raw in stdout:
                    text = raw.rstrip()
                    for secret in secrets:
                        text = text.replace(secret, "[REDACTED]")
                    print(text, flush=True)
                print("EXIT " + str(stdout.channel.recv_exit_status()), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
