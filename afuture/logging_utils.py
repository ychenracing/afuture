"""统一日志配置。"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(path: str | Path, level: str = "INFO") -> logging.Logger:
    """同时输出控制台和滚动文件，防止长期运行日志无限增长。"""
    logger = logging.getLogger("afuture")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
