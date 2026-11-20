import json
import logging
import logging.handlers
import os
import time


class Format(logging.Formatter):
    def format(self, record):
        row = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "pid": os.getpid(),
        }
        if getattr(record, "plugin_id", None):
            row["plugin_id"] = record.plugin_id
        if record.exc_info:
            row["exception"] = self.formatException(record.exc_info)
        return json.dumps(row, ensure_ascii=False)


def configure(directory):
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.WatchedFileHandler(
        directory / "opsmeshd.log", encoding="utf8"
    )
    handler.setFormatter(Format())
    logger = logging.getLogger("opsmesh.service")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


def lines(path, plugin, follow):
    with path.open(encoding="utf8") as f:
        while True:
            line = f.readline()
            if line:
                if not plugin or json.loads(line).get("plugin_id") == plugin:
                    print(line.rstrip(), flush=True)
            elif not follow:
                return
            else:
                time.sleep(0.3)
                if path.exists() and not os.path.samestat(
                    os.fstat(f.fileno()), path.stat()
                ):
                    break
        # Reopen after rotation, keeping only the current descriptor.
    return True
