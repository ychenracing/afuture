from afuture.models import ContractPosition
from afuture.reconcile import compare_positions


def test_reconcile_detects_position_drift():
    local = [ContractPosition(symbol="m2609", exchange="DCE", long_today=1)]
    remote = [ContractPosition(symbol="m2609", exchange="DCE", long_today=2)]
    result = compare_positions(local, remote)
    assert not result.matched
    assert "m2609" in result.details
