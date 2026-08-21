from types import SimpleNamespace

from afuture.broker.ctp import CtpBroker, CtpCredentials, build_ctp_setting
from afuture.models import Offset, OrderRequest, OrderSide, OrderType


def credentials():
    return CtpCredentials("user","secret","9999","tcp://td","tcp://md","app","auth","test")


def test_ctp_setting_matches_gateway_contract():
    setting=build_ctp_setting(credentials())
    assert setting["用户名"]=="user" and setting["经纪商代码"]=="9999" and setting["柜台环境"]=="测试"


def test_ctp_maps_internal_order_with_fake_runtime():
    broker=CtpBroker(credentials())
    class EnumValue:
        def __init__(self,value): self.value=value
    class Exchange: DCE=EnumValue("DCE")
    class Direction: LONG="LONG"; SHORT="SHORT"
    class VOffset: OPEN="OPEN"; CLOSE="CLOSE"; CLOSETODAY="CLOSETODAY"; CLOSEYESTERDAY="CLOSEYESTERDAY"
    class VType: LIMIT="LIMIT"; FAK="FAK"; FOK="FOK"
    class Req:
        def __init__(self,**kwargs): self.__dict__.update(kwargs)
    broker._runtime={"Exchange":Exchange,"Direction":Direction,"Offset":VOffset,"OrderType":VType,"OrderRequest":Req}
    req=broker._to_vnpy_order(OrderRequest("m2609","DCE",OrderSide.SELL,Offset.CLOSE,2,3000,OrderType.FAK,"pair"))
    assert (req.symbol,req.direction,req.offset,req.type)==("m2609","SHORT","CLOSE","FAK")


def test_ctp_account_margin_proxy_and_position_snapshot():
    broker=CtpBroker(credentials()); broker._trading_day="20260821"
    account=broker._convert_account(SimpleNamespace(balance=500000,available=350000,frozen=20000))
    assert account.margin==150000
    raw=[
        SimpleNamespace(symbol="m2609",exchange=SimpleNamespace(value="DCE"),direction=SimpleNamespace(name="LONG"),volume=3,yd_volume=1,price=3000.0),
        SimpleNamespace(symbol="m2609",exchange=SimpleNamespace(value="DCE"),direction=SimpleNamespace(name="SHORT"),volume=2,yd_volume=2,price=3010.0),
    ]
    broker._handle_position_snapshot(raw); p=broker.get_positions()[0]
    assert (p.long_today,p.long_yesterday,p.short_today,p.short_yesterday)==(2,1,0,2)
    assert broker.poll_events()[0].event_type=="position_snapshot"


def test_ctp_snapshot_generation_order_ownership_and_health():
    broker=CtpBroker(credentials(),snapshot_stale_seconds=20)
    broker._last_account=object(); broker._account_event_generation=4; broker._position_snapshot_generation=7
    marker=broker.snapshot_marker(); assert not broker.snapshot_ready(marker)
    broker._account_event_generation+=1; assert not broker.snapshot_ready(marker)
    broker._position_snapshot_generation+=1; assert broker.snapshot_ready(marker)
    broker._order_references["CTP.1"]="p"; assert broker.owns_order("CTP.1") and not broker.owns_order("manual")
    broker._last_account_monotonic=100; broker._last_position_snapshot_monotonic=100; broker.is_ready=lambda:True
    assert broker.health_error(now_monotonic=110) is None
    assert "stale" in broker.health_error(now_monotonic=121)
