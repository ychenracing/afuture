from pathlib import Path

from afuture.state import RuntimeState, StateStore


def test_kill_switch_persists_and_requires_reconciliation_to_clear(tmp_path: Path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.save(RuntimeState(kill_switch=True, kill_reason="disconnect", reconciled=False))
    loaded = store.load()
    assert loaded.kill_switch
    assert not store.can_clear_kill_switch(loaded)
    loaded.reconciled = True
    assert store.can_clear_kill_switch(loaded)


def test_state_persists_equity_high_watermark(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.save(RuntimeState(equity_high_watermark=612345.0))
    assert store.load().equity_high_watermark == 612345.0
