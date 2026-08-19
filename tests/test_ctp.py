from afuture.broker.ctp import CtpBroker, CtpCredentials, build_ctp_setting
from afuture.models import Offset, OrderRequest, OrderSide, OrderType


def test_ctp_setting_matches_gateway_contract():
    credentials = CtpCredentials(
        user_id="user",
        password="secret",
        broker_id="9999",
        td_address="tcp://td",
        md_address="tcp://md",
        app_id="app",
        auth_code="auth",
        environment="test",
    )
    setting = build_ctp_setting(credentials)
    assert setting["用户名"] == "user"
    assert setting["经纪商代码"] == "9999"
    assert setting["柜台环境"] == "测试"


def test_ctp_maps_internal_order_to_vnpy_request_with_fake_runtime():
    broker = CtpBroker(CtpCredentials("u", "p", "b", "td", "md", "a", "c", "test"))

    class EnumValue:
        def __init__(self, value): self.value = value

    class FakeExchange:
        DCE = EnumValue("DCE")

    class FakeDirection:
        LONG = "LONG"
        SHORT = "SHORT"

    class FakeOffset:
        OPEN = "OPEN"
        CLOSE = "CLOSE"
        CLOSETODAY = "CLOSETODAY"
        CLOSEYESTERDAY = "CLOSEYESTERDAY"

    class FakeOrderType:
        LIMIT = "LIMIT"
        FAK = "FAK"
        FOK = "FOK"

    class FakeOrderRequest:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)

    broker._runtime = {
        "Exchange": FakeExchange,
        "Direction": FakeDirection,
        "Offset": FakeOffset,
        "OrderType": FakeOrderType,
        "OrderRequest": FakeOrderRequest,
    }
    req = broker._to_vnpy_order(OrderRequest("m2609", "DCE", OrderSide.SELL, Offset.CLOSE, 2, 3000, OrderType.FAK, "pair"))
    assert req.symbol == "m2609"
    assert req.direction == "SHORT"
    assert req.offset == "CLOSE"
    assert req.type == "FAK"


def test_ctp_account_margin_is_conservative_without_explicit_margin_field():
    from types import SimpleNamespace
    from afuture.broker.ctp import CtpBroker, CtpCredentials

    broker = CtpBroker(CtpCredentials("u", "p", "b", "td", "md", "", ""))
    broker._trading_day = "20260819"
    raw = SimpleNamespace(balance=500000, available=350000, frozen=20000)
    converted = broker._convert_account(raw)
    # VeighNa AccountData 不暴露 CTP CurrMargin，故不能从差额中再扣 frozen。
    assert converted.margin == 150000


def test_ctp_position_snapshot_replaces_mirror_and_marks_generation():
    from types import SimpleNamespace

    broker = CtpBroker(CtpCredentials("u", "p", "b", "td", "md", "", ""))
    raw = [
        SimpleNamespace(
            symbol="m2609",
            exchange=SimpleNamespace(value="DCE"),
            direction=SimpleNamespace(name="LONG"),
            volume=3,
            yd_volume=1,
            price=3000.0,
        ),
        SimpleNamespace(
            symbol="m2609",
            exchange=SimpleNamespace(value="DCE"),
            direction=SimpleNamespace(name="SHORT"),
            volume=2,
            yd_volume=2,
            price=3010.0,
        ),
    ]

    broker._handle_position_snapshot(raw)

    position = broker.get_positions()[0]
    assert (position.long_today, position.long_yesterday) == (2, 1)
    assert (position.short_today, position.short_yesterday) == (0, 2)
    assert broker._position_snapshot_generation == 1
    event = broker.poll_events()[0]
    assert event.event_type == "position_snapshot"


def test_ctp_snapshot_ready_requires_fresh_account_and_position_generations():
    broker = CtpBroker(CtpCredentials("u", "p", "b", "td", "md", "", ""))
    broker._last_account = object()
    broker._account_event_generation = 4
    broker._position_snapshot_generation = 7
    marker = broker.snapshot_marker()

    assert not broker.snapshot_ready(marker)
    broker._account_event_generation += 1
    assert not broker.snapshot_ready(marker)
    broker._position_snapshot_generation += 1
    assert broker.snapshot_ready(marker)


def test_ctp_owns_only_orders_submitted_by_current_process():
    broker = CtpBroker(CtpCredentials("u", "p", "b", "td", "md", "", ""))
    broker._order_references["CTP.1"] = "m_pair"

    assert broker.owns_order("CTP.1")
    assert not broker.owns_order("CTP.manual")


def test_ctp_health_detects_stale_account_or_position_snapshot():
    broker = CtpBroker(
        CtpCredentials("u", "p", "b", "td", "md", "", ""),
        snapshot_stale_seconds=20.0,
    )
    broker._last_account_monotonic = 100.0
    broker._last_position_snapshot_monotonic = 100.0
    broker.is_ready = lambda: True

    assert broker.health_error(now_monotonic=110.0) is None
    assert "stale" in broker.health_error(now_monotonic=121.0)
