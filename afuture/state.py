"""运行状态持久化。

停机开关采用磁盘持久化，防止进程重启后自动恢复发单。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import ContractPosition


@dataclass
class RuntimeState:
    kill_switch: bool = False
    kill_reason: str = ""
    reconciled: bool = False
    trading_day: str = ""
    day_start_equity: float = 0.0
    equity_high_watermark: float = 0.0
    positions: list[dict] = field(default_factory=list)
    strategy_states: dict[str, dict] = field(default_factory=dict)


class StateStore:
    """使用原子替换避免异常退出留下半截 JSON。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return RuntimeState(**data)

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(state), ensure_ascii=False, indent=2, sort_keys=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        temp_path.replace(self.path)

    def save_positions(self, state: RuntimeState, positions: list[ContractPosition]) -> None:
        state.positions = [asdict(position) for position in positions]
        self.save(state)

    def positions_from_state(self, state: RuntimeState) -> list[ContractPosition]:
        return [ContractPosition(**item) for item in state.positions]

    @staticmethod
    def can_clear_kill_switch(state: RuntimeState) -> bool:
        return state.kill_switch and state.reconciled
