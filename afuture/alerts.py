"""生产告警通道。关键风险事件应独立于普通运行日志保存和通知。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import Request, urlopen


class MemoryAlertSink:
    """测试和嵌入场景使用的内存告警接收器。"""
    def __init__(self) -> None:
        self.events: list[dict] = []
    def send(self, event: dict) -> None:
        self.events.append(dict(event))


class FileAlertSink:
    """追加写 JSONL，确保即使外部通知失败仍保留本地证据。"""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
    def send(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


class WebhookAlertSink:
    """通用 JSON webhook；网络错误由 AlertManager 隔离，不反向阻塞风控。"""
    def __init__(self, url: str, timeout_seconds: float = 3.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
    def send(self, event: dict) -> None:
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        request = Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            response.read(1)


class AlertManager:
    """把同一个风险事件广播到多个告警通道；单个通道失败不影响其他通道。"""
    def __init__(self, sinks: list[object] | None = None) -> None:
        self.sinks = list(sinks or [])

    def emit(self, level: str, message: str, details: dict | None = None) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "message": message,
            "details": details or {},
        }
        for sink in self.sinks:
            try:
                sink.send(event)
            except Exception:
                # 告警故障不能阻止风控动作本身。
                continue

    def critical(self, message: str, details: dict | None = None) -> None:
        self.emit("CRITICAL", message, details)
