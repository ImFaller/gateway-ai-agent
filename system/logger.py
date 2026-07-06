import json
import logging
import os
import tempfile
import time
from pathlib import Path


class AgentLogger:
    def __init__(self, name="gateway-ai-agent", log_dir=None, level=logging.INFO):
        self.name = name
        self.log_dir = log_dir or tempfile.gettempdir()
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.handlers.clear()

        # Try file handler, fallback to console-only
        try:
            log_path = os.path.join(self.log_dir, f"{name}.log")
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))
            self.logger.addHandler(fh)
        except (PermissionError, OSError):
            pass

        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))
        self.logger.addHandler(ch)

        self._structured_logs = []

    def _log(self, level, message, extra=None):
        record = {"timestamp": time.time(), "level": level, "message": message, "extra": extra or {}}
        self._structured_logs.append(record)
        log_func = getattr(self.logger, level.lower(), self.logger.info)
        extra_str = (" | " + json.dumps(extra, ensure_ascii=False)) if extra else ""
        log_func(f"{message}{extra_str}")

    def info(self, message, extra=None):
        self._log("INFO", message, extra)
    def warning(self, message, extra=None):
        self._log("WARNING", message, extra)
    def error(self, message, extra=None):
        self._log("ERROR", message, extra)
    def debug(self, message, extra=None):
        self._log("DEBUG", message, extra)

    def query(self, level=None, start_time=None, end_time=None, limit=100):
        result = self._structured_logs
        if level:
            result = [r for r in result if r["level"] == level.upper()]
        if start_time:
            result = [r for r in result if r["timestamp"] >= start_time]
        if end_time:
            result = [r for r in result if r["timestamp"] <= end_time]
        return result[-limit:]

    def get_recent(self, count=20):
        return self._structured_logs[-count:]

    def get_statistics(self):
        total = len(self._structured_logs)
        levels = {}
        for r in self._structured_logs:
            levels[r["level"]] = levels.get(r["level"], 0) + 1
        return {"total": total, "by_level": levels}
