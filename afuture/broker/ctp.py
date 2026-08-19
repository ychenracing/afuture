"""基于 VeighNa ``vnpy_ctp`` 的 CTP 柜台适配器。

个人期货账户通常通过期货公司 CTP 前置接入交易所，而不是直接连接交易所。
本模块只在运行实盘命令时导入 VeighNa，因此研究和测试环境无需安装 CTP 二进制依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from queue import Empty, Queue
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from .base import Broker
from ..models import (
    AccountSnapshot,
    BrokerEvent,
    ContractPosition,
    Offset,
    Order,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Tick,
    Trade,
)
from ..position import PositionBook


@dataclass(frozen=True)
class CtpCredentials:
    """CTP 连接参数。敏感字段应从环境变量注入。"""

    user_id: str
    password: str
    broker_id: str
    td_address: str
    md_address: str
    app_id: str
    auth_code: str
    environment: str = "test"


def build_ctp_setting(credentials: CtpCredentials) -> dict[str, str]:
    """构造 vnpy_ctp 当前 ``CtpGateway`` 所需配置。"""
    env = "测试" if credentials.environment.lower() == "test" else "实盘"
    return {
        "用户名": credentials.user_id,
        "密码": credentials.password,
        "经纪商代码": credentials.broker_id,
        "交易服务器": credentials.td_address,
        "行情服务器": credentials.md_address,
        "产品名称": credentials.app_id,
        "授权编码": credentials.auth_code,
        "柜台环境": env,
    }


class CtpBroker(Broker):
    """把 VeighNa CTP 事件和对象转换为 afuture 的内部模型。"""

    gateway_name = "CTP"

    def __init__(
        self, credentials: CtpCredentials, *, snapshot_stale_seconds: float = 20.0
    ) -> None:
        if snapshot_stale_seconds <= 0:
            raise ValueError("snapshot_stale_seconds must be positive")
        self.credentials = credentials
        self.snapshot_stale_seconds = snapshot_stale_seconds
        self._runtime: dict[str, Any] | None = None
        self._event_engine = None
        self._main_engine = None
        self._events: Queue[BrokerEvent] = Queue()
        self._order_references: dict[str, str] = {}
        self._last_account: AccountSnapshot | None = None
        self._positions: dict[str, ContractPosition] = {}
        self._trading_day = ""
        self._account_event_generation = 0
        self._position_snapshot_generation = 0
        self._last_account_monotonic = 0.0
        self._last_position_snapshot_monotonic = 0.0

    def _load_runtime(self) -> dict[str, Any]:
        """延迟加载实盘依赖，并为 CTP 持仓查询增加完整快照完成事件。"""
        if self._runtime is not None:
            return self._runtime
        try:
            from vnpy.event import EventEngine
            from vnpy.trader.constant import (
                Direction,
                Exchange,
                Offset as VnOffset,
                OrderType as VnOrderType,
                Status,
            )
            from vnpy.trader.engine import MainEngine
            from vnpy.trader.event import EVENT_ACCOUNT, EVENT_ORDER, EVENT_TICK, EVENT_TRADE
            from vnpy.trader.object import OrderRequest as VnOrderRequest, SubscribeRequest
            from vnpy_ctp.gateway.ctp_gateway import CtpGateway, CtpTdApi
        except ImportError as exc:
            raise RuntimeError(
                "CTP live dependencies are missing; install with: pip install -e '.[live]'"
            ) from exc

        class TrackedCtpTdApi(CtpTdApi):
            """在官方 CTP 回调之上暴露一次完整持仓查询的边界。"""

            def onRspQryInvestorPosition(self, data, error, reqid, last):
                error_id = int((error or {}).get("ErrorID", 0))
                if error_id:
                    super().onRspQryInvestorPosition(data, error, reqid, last)
                    return
                if not last:
                    super().onRspQryInvestorPosition(data, error, reqid, False)
                    return

                # 最后一条仍需先进入官方聚合逻辑，但不能让官方逻辑先清空缓存。
                if data:
                    super().onRspQryInvestorPosition(data, error, reqid, False)
                snapshot = list(self.positions.values())
                for position in snapshot:
                    self.gateway.on_position(position)
                self.positions.clear()

                callback = getattr(
                    self.gateway, "_afuture_position_snapshot_callback", None
                )
                if callable(callback):
                    callback(snapshot)

        class TrackedCtpGateway(CtpGateway):
            """使用带完整快照边界的交易 API，其他行为保持官方实现。"""

            default_name = "CTP"

            def __init__(self, event_engine, gateway_name):
                super().__init__(event_engine, gateway_name)
                self._afuture_position_snapshot_callback = None
                # ``super`` 创建的交易 API 尚未连接，直接替换不会留下活动会话。
                self.td_api = TrackedCtpTdApi(self)

        self._runtime = {
            "EventEngine": EventEngine,
            "MainEngine": MainEngine,
            "CtpGateway": TrackedCtpGateway,
            "Direction": Direction,
            "Exchange": Exchange,
            "Offset": VnOffset,
            "OrderType": VnOrderType,
            "Status": Status,
            "OrderRequest": VnOrderRequest,
            "SubscribeRequest": SubscribeRequest,
            "EVENT_ACCOUNT": EVENT_ACCOUNT,
            "EVENT_ORDER": EVENT_ORDER,
            "EVENT_TICK": EVENT_TICK,
            "EVENT_TRADE": EVENT_TRADE,
        }
        return self._runtime

    def start(self) -> None:
        runtime = self._load_runtime()
        self._event_engine = runtime["EventEngine"]()
        self._main_engine = runtime["MainEngine"](self._event_engine)
        self._main_engine.add_gateway(runtime["CtpGateway"])
        gateway = self._main_engine.get_gateway(self.gateway_name)
        if gateway is None:
            raise RuntimeError("CTP gateway could not be created")
        gateway._afuture_position_snapshot_callback = self._handle_position_snapshot

        self._event_engine.register(runtime["EVENT_TICK"], self._on_tick)
        self._event_engine.register(runtime["EVENT_ORDER"], self._on_order)
        self._event_engine.register(runtime["EVENT_TRADE"], self._on_trade)
        self._event_engine.register(runtime["EVENT_ACCOUNT"], self._on_account)
        self._main_engine.connect(build_ctp_setting(self.credentials), self.gateway_name)

    def stop(self) -> None:
        if self._main_engine is not None:
            self._main_engine.close()
        self._main_engine = None
        self._event_engine = None

    def is_ready(self) -> bool:
        if self._main_engine is None:
            return False
        gateway = self._main_engine.get_gateway(self.gateway_name)
        if gateway is None:
            return False
        td_api = getattr(gateway, "td_api", None)
        md_api = getattr(gateway, "md_api", None)
        return bool(
            td_api
            and md_api
            and getattr(td_api, "login_status", False)
            and getattr(td_api, "contract_inited", False)
            and getattr(md_api, "login_status", False)
        )

    def health_error(self, *, now_monotonic: float | None = None) -> str | None:
        """检测账户和持仓周期快照是否停止刷新。"""
        if not self.is_ready():
            return None
        now = monotonic() if now_monotonic is None else now_monotonic
        if self._last_account_monotonic <= 0 or self._last_position_snapshot_monotonic <= 0:
            return "CTP account/position snapshot is not initialized"
        if now - self._last_account_monotonic > self.snapshot_stale_seconds:
            return "CTP account snapshot is stale"
        if now - self._last_position_snapshot_monotonic > self.snapshot_stale_seconds:
            return "CTP position snapshot is stale"
        return None

    def snapshot_marker(self) -> tuple[int, int]:
        """记录当前账户事件和完整持仓快照代次，供启动安全门等待新快照。"""
        return self._account_event_generation, self._position_snapshot_generation

    def snapshot_ready(self, marker: tuple[int, int]) -> bool:
        """只有账户和持仓都在 marker 之后完整刷新才算启动快照有效。"""
        account_generation, position_generation = marker
        return bool(
            self._last_account is not None
            and self._account_event_generation > account_generation
            and self._position_snapshot_generation > position_generation
        )

    def subscribe(self, symbol: str, exchange: str) -> None:
        if self._main_engine is None:
            raise RuntimeError("CTP broker is not started")
        runtime = self._load_runtime()
        req = runtime["SubscribeRequest"](
            symbol=symbol, exchange=self._exchange(exchange)
        )
        self._main_engine.subscribe(req, self.gateway_name)

    def send_order(self, request: OrderRequest) -> str:
        if not self.is_ready() or self._main_engine is None:
            raise RuntimeError("CTP market/trading session is not ready")
        vn_request = self._to_vnpy_order(request)
        order_id = self._main_engine.send_order(vn_request, self.gateway_name)
        if not order_id:
            raise RuntimeError("CTP order request was not accepted by gateway")
        self._order_references[order_id] = request.reference
        return order_id

    def owns_order(self, order_id: str) -> bool:
        """只承认当前进程实际提交并记录过的委托。"""
        return order_id in self._order_references

    def cancel_order(self, order_id: str) -> None:
        if self._main_engine is None:
            return
        order = self._main_engine.get_order(order_id)
        if order is None:
            return
        self._main_engine.cancel_order(order.create_cancel_request(), self.gateway_name)

    def get_order(self, order_id: str) -> Order | None:
        if self._main_engine is None:
            return None
        raw = self._main_engine.get_order(order_id)
        return self._convert_order(raw) if raw else None

    def get_active_orders(self) -> list[Order]:
        if self._main_engine is None:
            return []
        return [
            self._convert_order(order)
            for order in self._main_engine.get_all_active_orders()
        ]

    def get_account(self) -> AccountSnapshot:
        if self._main_engine is not None:
            accounts = self._main_engine.get_all_accounts()
            if accounts:
                self._last_account = self._convert_account(accounts[0])
        if self._last_account is None:
            raise RuntimeError("CTP account snapshot is not available")
        return self._last_account

    def get_positions(self) -> list[ContractPosition]:
        """返回最近一次完整快照叠加本进程成交后的镜像，不读取 OMS 残留仓位。"""
        return [replace(position) for position in self._positions.values() if not position.empty]

    def poll_events(self) -> list[BrokerEvent]:
        result: list[BrokerEvent] = []
        while True:
            try:
                result.append(self._events.get_nowait())
            except Empty:
                return result

    def get_trading_day(self) -> str:
        if self._main_engine is not None:
            gateway = self._main_engine.get_gateway(self.gateway_name)
            td_api = getattr(gateway, "td_api", None) if gateway else None
            getter = getattr(td_api, "getTradingDay", None)
            if callable(getter):
                value = getter()
                if isinstance(value, bytes):
                    value = value.decode("ascii", errors="ignore")
                if value:
                    self._trading_day = str(value)
        return self._trading_day or datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y%m%d"
        )

    def _to_vnpy_order(self, request: OrderRequest):
        runtime = self._load_runtime()
        direction = (
            runtime["Direction"].LONG
            if request.side is OrderSide.BUY
            else runtime["Direction"].SHORT
        )
        offset_map = {
            Offset.OPEN: runtime["Offset"].OPEN,
            Offset.CLOSE: runtime["Offset"].CLOSE,
            Offset.CLOSE_TODAY: runtime["Offset"].CLOSETODAY,
            Offset.CLOSE_YESTERDAY: runtime["Offset"].CLOSEYESTERDAY,
        }
        type_map = {
            OrderType.LIMIT: runtime["OrderType"].LIMIT,
            OrderType.FAK: runtime["OrderType"].FAK,
            OrderType.FOK: runtime["OrderType"].FOK,
        }
        return runtime["OrderRequest"](
            symbol=request.symbol,
            exchange=self._exchange(request.exchange),
            direction=direction,
            type=type_map[request.order_type],
            volume=request.volume,
            price=request.price,
            offset=offset_map[request.offset],
            reference=request.reference,
        )

    def _exchange(self, exchange: str):
        runtime = self._load_runtime()
        try:
            return getattr(runtime["Exchange"], exchange)
        except AttributeError as exc:
            raise ValueError(f"unsupported exchange: {exchange}") from exc

    def _on_tick(self, event) -> None:
        raw = event.data
        tick = Tick(
            symbol=raw.symbol,
            exchange=raw.exchange.value,
            timestamp=raw.datetime,
            bid_price=float(raw.bid_price_1),
            ask_price=float(raw.ask_price_1),
            last_price=float(raw.last_price),
            bid_volume=float(raw.bid_volume_1),
            ask_volume=float(raw.ask_volume_1),
            trading_day=self.get_trading_day(),
            limit_up=float(getattr(raw, "limit_up", 0.0) or 0.0),
            limit_down=float(getattr(raw, "limit_down", 0.0) or 0.0),
        )
        try:
            tick.validate()
        except ValueError:
            return
        self._events.put(BrokerEvent("tick", tick))

    def _on_order(self, event) -> None:
        self._events.put(BrokerEvent("order", self._convert_order(event.data)))

    def _on_trade(self, event) -> None:
        raw = event.data
        side = (
            OrderSide.BUY
            if getattr(raw.direction, "name", "").upper() == "LONG"
            else OrderSide.SELL
        )
        offset_name = getattr(raw.offset, "name", "CLOSE").upper()
        offset = {
            "OPEN": Offset.OPEN,
            "CLOSE": Offset.CLOSE,
            "CLOSETODAY": Offset.CLOSE_TODAY,
            "CLOSEYESTERDAY": Offset.CLOSE_YESTERDAY,
        }.get(offset_name, Offset.CLOSE)
        trade = Trade(
            trade_id=raw.vt_tradeid,
            order_id=raw.vt_orderid,
            symbol=raw.symbol,
            exchange=raw.exchange.value,
            side=side,
            offset=offset,
            volume=int(raw.volume),
            price=float(raw.price),
            timestamp=raw.datetime or datetime.now(ZoneInfo("Asia/Shanghai")),
        )
        try:
            book = PositionBook(self.get_positions())
            book.apply_trade(trade)
            self._positions = {position.symbol: position for position in book.all()}
        except Exception as exc:
            self._events.put(
                BrokerEvent("broker_error", f"CTP trade mirror update failed: {exc}")
            )
        self._events.put(BrokerEvent("trade", trade))

    def _on_account(self, event) -> None:
        self._last_account = self._convert_account(event.data)
        self._account_event_generation += 1
        self._last_account_monotonic = monotonic()
        self._events.put(BrokerEvent("account", self._last_account))

    def _handle_position_snapshot(self, raw_positions: list[object]) -> None:
        """用一次 CTP 查询的完整结果原子替换仓位镜像；空列表同样有效。"""
        combined: dict[str, ContractPosition] = {}
        try:
            for raw in raw_positions:
                volume = int(raw.volume)
                yesterday = int(raw.yd_volume)
                if volume < 0 or yesterday < 0 or yesterday > volume:
                    raise ValueError(f"invalid CTP position volume for {raw.symbol}")
                today = volume - yesterday
                position = combined.setdefault(
                    raw.symbol,
                    ContractPosition(raw.symbol, raw.exchange.value),
                )
                direction_value = getattr(
                    raw.direction, "name", str(raw.direction)
                ).upper()
                if "LONG" in direction_value:
                    position.long_today += today
                    position.long_yesterday += yesterday
                    position.long_price = float(raw.price)
                elif "SHORT" in direction_value:
                    position.short_today += today
                    position.short_yesterday += yesterday
                    position.short_price = float(raw.price)
                else:
                    raise ValueError(f"unsupported CTP position direction: {direction_value}")
        except Exception as exc:
            self._events.put(BrokerEvent("broker_error", str(exc)))
            return

        self._positions = {
            symbol: position for symbol, position in combined.items() if not position.empty
        }
        self._position_snapshot_generation += 1
        self._last_position_snapshot_monotonic = monotonic()
        self._events.put(BrokerEvent("position_snapshot", self.get_positions()))

    def _convert_account(self, raw) -> AccountSnapshot:
        balance = float(raw.balance)
        available = float(raw.available)
        # VeighNa AccountData 不暴露 CTP CurrMargin，以权益与可用资金差额作为保守占用代理。
        margin = max(0.0, balance - available)
        return AccountSnapshot(
            balance=balance,
            equity=balance,
            available=available,
            margin=margin,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            trading_day=self.get_trading_day(),
        )

    def _convert_order(self, raw) -> Order:
        runtime = self._load_runtime()
        status_map = {
            runtime["Status"].SUBMITTING: OrderStatus.SUBMITTING,
            runtime["Status"].NOTTRADED: OrderStatus.NOT_TRADED,
            runtime["Status"].PARTTRADED: OrderStatus.PART_TRADED,
            runtime["Status"].ALLTRADED: OrderStatus.FILLED,
            runtime["Status"].CANCELLED: OrderStatus.CANCELLED,
            runtime["Status"].REJECTED: OrderStatus.REJECTED,
        }
        side = (
            OrderSide.BUY
            if getattr(raw.direction, "name", "").upper() == "LONG"
            else OrderSide.SELL
        )
        offset_name = getattr(raw.offset, "name", "CLOSE").upper()
        offset = {
            "OPEN": Offset.OPEN,
            "CLOSE": Offset.CLOSE,
            "CLOSETODAY": Offset.CLOSE_TODAY,
            "CLOSEYESTERDAY": Offset.CLOSE_YESTERDAY,
        }.get(offset_name, Offset.CLOSE)
        type_name = getattr(raw.type, "name", "LIMIT").upper()
        order_type = {
            "FAK": OrderType.FAK,
            "FOK": OrderType.FOK,
        }.get(type_name, OrderType.LIMIT)
        reference = getattr(raw, "reference", "") or self._order_references.get(
            raw.vt_orderid, ""
        )
        request = OrderRequest(
            symbol=raw.symbol,
            exchange=raw.exchange.value,
            side=side,
            offset=offset,
            volume=int(raw.volume),
            price=float(raw.price),
            order_type=order_type,
            reference=reference,
        )
        return Order(
            order_id=raw.vt_orderid,
            request=request,
            status=status_map.get(raw.status, OrderStatus.REJECTED),
            traded=int(raw.traded),
            average_price=float(raw.price),
        )
