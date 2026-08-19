import json
from pathlib import Path

from afuture.config import AppConfig
from afuture.models import ContractSpec, PairConfig
from afuture.replay import run_replay
from afuture.risk import RiskConfig
from afuture.state import RuntimeState, StateStore


def test_replay_resets_runtime_state_and_writes_performance(tmp_path: Path):
    state_path = tmp_path / "state.json"
    report_path = tmp_path / "report.json"
    StateStore(state_path).save(RuntimeState(kill_switch=True, kill_reason="old"))
    specs = {
        "m2609": ContractSpec("m2609", "DCE", 10, 1, 0.1, 0.1),
        "m2701": ContractSpec("m2701", "DCE", 10, 1, 0.1, 0.1),
    }
    pair = PairConfig("m_pair", "m2609", "m2701", "DCE", 1, lookback=2, entry_z=0.5, exit_z=0.2)
    config = AppConfig("replay", 500000, specs, [pair], RiskConfig(), None, state_path=str(state_path), report_path=str(report_path))
    data_path = tmp_path / "ticks.csv"
    data_path.write_text(
        "timestamp,symbol,exchange,bid_price,ask_price,last_price,bid_volume,ask_volume,trading_day\n"
        "2026-08-17T09:00:00+08:00,m2609,DCE,3009,3011,3010,10,10,20260817\n"
        "2026-08-17T09:00:00+08:00,m2701,DCE,2999,3001,3000,10,10,20260817\n"
        "2026-08-18T09:00:00+08:00,m2609,DCE,3009,3011,3010,10,10,20260818\n"
        "2026-08-18T09:00:00+08:00,m2701,DCE,2999,3001,3000,10,10,20260818\n"
        "2026-08-19T09:00:00+08:00,m2609,DCE,3019,3021,3020,10,10,20260819\n"
        "2026-08-19T09:00:00+08:00,m2701,DCE,2999,3001,3000,10,10,20260819\n",
        encoding="utf-8",
    )
    run_replay(config, data_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert "performance" in payload
    assert payload["performance"]["trading_days"] == 3
    assert not StateStore(state_path).load().kill_switch
