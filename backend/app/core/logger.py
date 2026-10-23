import json
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

_ASIA_SHANGHAI = timezone(timedelta(hours=8))

# 请求与运行身份：由 HTTP 中间件和后台运行入口绑定，随 asyncio 任务与 to_thread 自动传递，
# 日志不必在每条消息里手写这些 ID 也能在 Loki 中按字段串起来。
_LOG_CONTEXT_FIELDS = ("request_id", "run_id", "conversation_id", "user_id")
_log_context: dict[str, ContextVar[str | None]] = {
    field: ContextVar(f"fusion_log_{field}", default=None) for field in _LOG_CONTEXT_FIELDS
}

# LogRecord 自带属性；其余属性来自调用方的 extra=，原样输出为 JSON 字段。
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "taskName", *_LOG_CONTEXT_FIELDS}
)


@contextmanager
def log_context(**fields: str | None) -> Generator[None, None, None]:
    """在当前上下文绑定日志关联字段，退出时恢复外层取值。"""
    tokens = []
    for name, value in fields.items():
        if name not in _log_context:
            raise ValueError(f"未知的日志上下文字段: {name}")
        tokens.append((_log_context[name], _log_context[name].set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current_log_context(name: str) -> str | None:
    """读取当前上下文绑定的关联字段，供需要向外部服务传递关联 ID 的调用使用。"""
    return _log_context[name].get()


class _LogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name, var in _log_context.items():
            if not hasattr(record, name):
                setattr(record, name, var.get())
        return True


class JsonFormatter(logging.Formatter):
    """每条日志一行 JSON，Loki 可直接用 `| json` 按 level、logger 和关联 ID 过滤。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, _ASIA_SHANGHAI).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in _LOG_CONTEXT_FIELDS:
            value = getattr(record, name, None)
            if value:
                payload[name] = value
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _configure_handler(handler: logging.Handler) -> logging.Handler:
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_LogContextFilter())
    return handler


def setup_logger(name, log_file, level=logging.INFO):
    """设置日志记录器"""
    # 确保日志目录存在
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    # 获取记录器
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 清空现有处理器，避免重复
    if logger.handlers:
        logger.handlers = []

    # 文件处理器与控制台处理器
    logger.addHandler(_configure_handler(RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5)))
    logger.addHandler(_configure_handler(logging.StreamHandler()))

    # 确保传播设置正确
    logger.propagate = False

    return logger


# 配置根日志记录器
def setup_root_logger():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清空现有处理器
    if root_logger.handlers:
        root_logger.handlers = []

    # 添加控制台处理器
    console = _configure_handler(logging.StreamHandler())
    console.setLevel(logging.INFO)
    root_logger.addHandler(console)


# 设置根日志记录器
setup_root_logger()

# 主应用日志
app_logger = setup_logger("app", "./logs/app.log")

# 输出一条明显的日志，帮助确认配置生效
app_logger.info("=== 日志系统初始化完成 ===")
