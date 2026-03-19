from __future__ import annotations

import logging
from contextvars import ContextVar, Token

_request_id_var: ContextVar[str] = ContextVar('nimarita_request_id', default='-')


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


def set_request_id(request_id: str) -> Token[str]:
    return _request_id_var.set(request_id or '-')


def reset_request_id(token: Token[str]) -> None:
    _request_id_var.reset(token)


def get_request_id() -> str:
    return _request_id_var.get()


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        logging.basicConfig(
            level=level,
            format='%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s',
        )

    request_filter = _RequestIdFilter()
    for handler in root.handlers:
        handler.addFilter(request_filter)
        if handler.formatter is None:
            handler.setFormatter(
                logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s')
            )
