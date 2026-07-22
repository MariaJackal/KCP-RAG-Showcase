import json
import logging
from datetime import datetime, timezone


_LOGGER = logging.getLogger("kcpd_rag")
if not _LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False


def _to_log_level(severity):
    sev = str(severity or "INFO").upper()
    if sev in ("CRITICAL", "FATAL"):
        return logging.CRITICAL
    if sev == "ERROR":
        return logging.ERROR
    if sev in ("WARNING", "WARN"):
        return logging.WARNING
    if sev == "DEBUG":
        return logging.DEBUG
    return logging.INFO


def _to_primitive(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _to_primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_primitive(v) for v in value]
    return str(value)


def log_event(event, severity="INFO", **fields):
    sev = str(severity or "INFO").upper()
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "severity": sev,
    }
    payload.update({k: _to_primitive(v) for k, v in fields.items()})
    _LOGGER.log(_to_log_level(sev), json.dumps(payload, ensure_ascii=False))


def classify_error(exc):
    text = str(exc).lower()
    if "credential" in text or "adc" in text:
        return "auth_credentials"
    if "permission" in text or "403" in text:
        return "auth_permission"
    if "timeout" in text or "deadline" in text:
        return "timeout"
    if "resource exhausted" in text or "429" in text:
        return "quota_rate_limit"
    return "unknown"
