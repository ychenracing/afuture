"""实际执行与候选质量证据。

把模型预期、Auto 候选判断和实际成交分开记录，避免只看账户收益而不知道
selector、手续费、滑点或裸腿修复中的哪一层吞掉 Edge。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median


class ExecutionQualityRecorder:
    """以 JSONL 保存 candidate/decision/round-trip，并提供轻量汇总。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record_round_trip(
        self,
        *,
        pair_id: str,
        expected_net_edge: float,
        realized_net_edge: float,
        expected_spread: float,
        entry_spread: float,
        exit_spread: float,
        commission: float,
        leg_latency_ms: float,
        partial_fill: bool,
        rollback: bool,
        reduce_only: bool,
        extra: dict | None = None,
    ) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "round_trip",
            "pair_id": pair_id,
            "expected_net_edge": float(expected_net_edge),
            "realized_net_edge": float(realized_net_edge),
            "expected_spread": float(expected_spread),
            "entry_spread": float(entry_spread),
            "exit_spread": float(exit_spread),
            "slippage": abs(float(entry_spread) - float(expected_spread)),
            "commission": float(commission),
            "leg_latency_ms": float(leg_latency_ms),
            "partial_fill": bool(partial_fill),
            "rollback": bool(rollback),
            "reduce_only": bool(reduce_only),
        }
        if extra:
            payload["extra"] = dict(extra)
        self._append(payload)

    def record_candidate(self, **fields) -> None:
        self._append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "candidate",
                **fields,
            }
        )

    def record_decision(self, **fields) -> None:
        self._append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "decision",
                **fields,
            }
        )

    def summary(self) -> dict:
        all_rows = self._read()
        rows = [row for row in all_rows if row.get("event") == "round_trip"]
        slippage = sorted(float(row.get("slippage", 0.0)) for row in rows)
        realized = [float(row.get("realized_net_edge", 0.0)) for row in rows]
        commissions = [float(row.get("commission", 0.0)) for row in rows]
        latencies = [float(row.get("leg_latency_ms", 0.0)) for row in rows]
        candidates = [row for row in all_rows if row.get("event") == "candidate"]
        decisions = [row for row in all_rows if row.get("event") == "decision"]
        return {
            "candidate_events": len(candidates),
            "decision_events": len(decisions),
            "round_trips": len(rows),
            "median_slippage": median(slippage) if slippage else 0.0,
            "p95_slippage": self._percentile(slippage, 0.95),
            "realized_edge_total": sum(realized),
            "commission_total": sum(commissions),
            "median_leg_latency_ms": median(latencies) if latencies else 0.0,
            "partial_fill_count": sum(bool(row.get("partial_fill")) for row in rows),
            "rollback_count": sum(bool(row.get("rollback")) for row in rows),
            "reduce_only_count": sum(bool(row.get("reduce_only")) for row in rows),
        }

    def _append(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        result: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    @staticmethod
    def _percentile(values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        index = max(0, min(len(values) - 1, int(round((len(values) - 1) * ratio))))
        return float(values[index])
