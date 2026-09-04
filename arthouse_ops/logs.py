"""One JSON object per line on stdout.

Every line carries the run id, so a whole run can be pulled out of a log with
one grep, and every line about a single form entry carries its entry_id. That
is what makes "what happened to entry 22801" answerable after the fact.
"""

import json
import logging
import os
import sys
import time
import uuid

RUN_ID = os.environ.get("GITHUB_RUN_ID") or uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "run_id": RUN_ID,
            "stage": getattr(record, "stage", "-"),
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(payload, ensure_ascii=False)


class StageLogger:
    """Logger bound to one pipeline stage. Keyword args become JSON fields."""

    def __init__(self, stage):
        self.stage = stage
        self._log = logging.getLogger("arthouse." + stage)

    def _emit(self, level, msg, exc_info=False, **fields):
        self._log.log(
            level, msg, exc_info=exc_info,
            extra={"stage": self.stage, "fields": fields},
        )

    def debug(self, msg, **fields):
        self._emit(logging.DEBUG, msg, **fields)

    def info(self, msg, **fields):
        self._emit(logging.INFO, msg, **fields)

    def warn(self, msg, **fields):
        self._emit(logging.WARNING, msg, **fields)

    def error(self, msg, exc_info=False, **fields):
        self._emit(logging.ERROR, msg, exc_info=exc_info, **fields)


def setup(level="INFO"):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("arthouse")
    root.handlers = [handler]
    root.setLevel(level)
    root.propagate = False


def get(stage):
    return StageLogger(stage)
