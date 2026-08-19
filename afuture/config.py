"""TOML 配置加载与实盘安全校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import re
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .broker.ctp import CtpCredentials
from .models import ContractSpec, FeeSpec, PairConfig
from .risk import RiskConfig


@dataclass(frozen=True)
class AppConfig:
    """应用运行配置。"""

    mode: str
    initial_capital: float
    contracts: dict[str, ContractSpec]
    pairs: list[PairConfig]
    risk: RiskConfig
    ctp: CtpCredentials | None
    slippage_ticks: int = 1
    aggressive_ticks: int = 1
    auto_flatten_imbalance: bool = True
    legging_timeout_seconds: float = 2.0
    state_path: str = "runtime/state.json"
    log_path: str = "runtime/afuture.log"
    report_path: str = "runtime/report.json"
    journal_path: str = "runtime/audit.jsonl"


def load_config(path: str | Path) -> AppConfig:
    """读取 TOML；账户敏感信息只从环境变量注入。"""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    system = data.get("system", {})
    mode = str(system.get("mode", "replay")).lower()
    if mode not in {"replay", "live"}:
        raise ValueError("system.mode must be replay or live")
    initial_capital = float(system.get("initial_capital", 500000))
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    risk_raw = data.get("risk", {})
    risk = RiskConfig(
        **{
            key: value
            for key, value in risk_raw.items()
            if key in RiskConfig.__dataclass_fields__
        }
    )

    contracts = _load_contracts(data.get("contracts", []))
    pairs = _load_pairs(data.get("pairs", []), contracts, mode)
    ctp = _load_ctp(data.get("ctp", {}), mode)

    execution = data.get("execution", {})
    slippage_ticks = int(execution.get("slippage_ticks", 1))
    aggressive_ticks = int(execution.get("aggressive_ticks", 1))
    legging_timeout_seconds = float(execution.get("legging_timeout_seconds", 2.0))
    if slippage_ticks < 0 or aggressive_ticks < 0 or legging_timeout_seconds < 0:
        raise ValueError("execution numeric limits cannot be negative")

    paths = data.get("paths", {})
    return AppConfig(
        mode=mode,
        initial_capital=initial_capital,
        contracts=contracts,
        pairs=pairs,
        risk=risk,
        ctp=ctp,
        slippage_ticks=slippage_ticks,
        aggressive_ticks=aggressive_ticks,
        auto_flatten_imbalance=bool(execution.get("auto_flatten_imbalance", True)),
        legging_timeout_seconds=legging_timeout_seconds,
        state_path=str(paths.get("state", "runtime/state.json")),
        log_path=str(paths.get("log", "runtime/afuture.log")),
        report_path=str(paths.get("report", "runtime/report.json")),
        journal_path=str(paths.get("journal", "runtime/audit.jsonl")),
    )


def _load_contracts(rows: list[dict]) -> dict[str, ContractSpec]:
    contracts: dict[str, ContractSpec] = {}
    for raw in rows:
        fee_raw = raw.get("fee", {})
        fee = FeeSpec(
            **{
                key: float(value)
                for key, value in fee_raw.items()
                if key in FeeSpec.__dataclass_fields__
            }
        )
        spec = ContractSpec(
            symbol=str(raw["symbol"]),
            exchange=str(raw["exchange"]).upper(),
            multiplier=float(raw["multiplier"]),
            price_tick=float(raw["price_tick"]),
            margin_rate_long=float(raw["margin_rate_long"]),
            margin_rate_short=float(raw["margin_rate_short"]),
            fee=fee,
        )
        if not spec.symbol or spec.symbol in contracts:
            raise ValueError(f"duplicate or empty contract symbol: {spec.symbol}")
        if spec.multiplier <= 0 or spec.price_tick <= 0:
            raise ValueError(f"invalid multiplier/price_tick: {spec.symbol}")
        if not 0 < spec.margin_rate_long < 1 or not 0 < spec.margin_rate_short < 1:
            raise ValueError(f"invalid margin rate: {spec.symbol}")
        if any(value < 0 for value in spec.fee.__dict__.values()):
            raise ValueError(f"fee cannot be negative: {spec.symbol}")
        contracts[spec.symbol] = spec
    return contracts


def _load_pairs(
    rows: list[dict], contracts: dict[str, ContractSpec], mode: str
) -> list[PairConfig]:
    pairs = [PairConfig(**raw) for raw in rows]
    pair_ids: set[str] = set()
    used_symbols: set[str] = set()
    for pair in pairs:
        if not pair.pair_id or pair.pair_id in pair_ids:
            raise ValueError(f"duplicate or empty pair_id: {pair.pair_id}")
        pair_ids.add(pair.pair_id)
        if pair.near_symbol == pair.far_symbol:
            raise ValueError(f"pair {pair.pair_id} uses the same contract twice")
        if pair.volume <= 0:
            raise ValueError(f"pair {pair.pair_id} volume must be positive")
        if pair.sample_seconds < 0:
            raise ValueError(f"pair {pair.pair_id} sample_seconds cannot be negative")
        if pair.lookback < 2 or not 0 <= pair.exit_z < pair.entry_z < pair.stop_z:
            raise ValueError(f"pair {pair.pair_id} has invalid z-score parameters")
        if _contract_root(pair.near_symbol) != _contract_root(pair.far_symbol):
            raise ValueError(f"pair {pair.pair_id} is not a same-product calendar spread")
        for symbol in (pair.near_symbol, pair.far_symbol):
            spec = contracts.get(symbol)
            if spec is None:
                raise ValueError(f"pair {pair.pair_id} missing contract spec: {symbol}")
            if spec.exchange != pair.exchange.upper():
                raise ValueError(f"pair {pair.pair_id} exchange does not match {symbol}")
            if symbol in used_symbols:
                raise ValueError(f"contract {symbol} is reused by multiple pairs")
            used_symbols.add(symbol)
        if mode == "live":
            if not pair.expiry_near or not pair.expiry_far:
                raise ValueError(f"pair {pair.pair_id} expiry dates are required in live mode")
            near_expiry = date.fromisoformat(pair.expiry_near)
            far_expiry = date.fromisoformat(pair.expiry_far)
            if near_expiry >= far_expiry:
                raise ValueError(f"pair {pair.pair_id} expiry_near must be before expiry_far")
    return pairs


def _load_ctp(raw: dict, mode: str) -> CtpCredentials | None:
    if mode != "live":
        return None
    required_env = {
        "user_id": "AFUTURE_CTP_USER",
        "password": "AFUTURE_CTP_PASSWORD",
        "broker_id": "AFUTURE_CTP_BROKER",
    }
    secrets = {name: os.getenv(env_name, "") for name, env_name in required_env.items()}
    missing = [env_name for name, env_name in required_env.items() if not secrets[name]]
    if missing:
        raise ValueError(f"missing CTP environment variables: {', '.join(missing)}")
    td_address = str(raw.get("td_address", "")).strip()
    md_address = str(raw.get("md_address", "")).strip()
    if not td_address or not md_address:
        raise ValueError("ctp td_address and md_address are required")
    environment = str(raw.get("environment", "test")).lower()
    if environment not in {"test", "production"}:
        raise ValueError("ctp.environment must be test or production")
    return CtpCredentials(
        **secrets,
        td_address=td_address,
        md_address=md_address,
        app_id=os.getenv("AFUTURE_CTP_APP_ID", ""),
        auth_code=os.getenv("AFUTURE_CTP_AUTH_CODE", ""),
        environment=environment,
    )


def _contract_root(symbol: str) -> str:
    match = re.match(r"([A-Za-z]+)", symbol)
    return match.group(1).lower() if match else symbol.lower()
