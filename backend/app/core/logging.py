import logging
import re
import sys

import structlog

SENSITIVE_KEYS = {
    'password',
    'new_password',
    'old_password',
    'confirm_password',
    'otp',
    'token',
    'access_token',
    'refresh_token',
    'authorization',
    'cookie',
    'secret',
    'api_key',
    'private_key',
}
EMAIL_RE = re.compile(r'([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+[.][A-Za-z]{2,})')


def _mask_email(value: str) -> str:
    return EMAIL_RE.sub(lambda m: m.group(1) + '***' + m.group(2), value)


def redact(_logger, _method, event_dict):
    for key, value in list(event_dict.items()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = '***'
        elif isinstance(value, str):
            event_dict[key] = _mask_email(value)
    return event_dict


def setup_logging(level: str = 'INFO', json_logs: bool = True) -> None:
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt='iso', utc=True),
            redact,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )

    root_level = max(logging.INFO, logging.getLevelName(level.upper()))
    logging.basicConfig(format='%(message)s', stream=sys.stdout, level=root_level)
