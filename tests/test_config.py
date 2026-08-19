from pathlib import Path

import pytest

from afuture.config import load_config


def test_load_config_reads_pairs_and_contracts(tmp_path: Path):
    config = tmp_path / "afuture.toml"
    config.write_text('''
[system]
mode = "replay"
initial_capital = 500000

[risk]
max_margin_ratio = 0.35

[[contracts]]
symbol = "m2609"
exchange = "DCE"
multiplier = 10
price_tick = 1
margin_rate_long = 0.12
margin_rate_short = 0.12

[[contracts]]
symbol = "m2701"
exchange = "DCE"
multiplier = 10
price_tick = 1
margin_rate_long = 0.12
margin_rate_short = 0.12

[[pairs]]
pair_id = "m_pair"
near_symbol = "m2609"
far_symbol = "m2701"
exchange = "DCE"
volume = 1
''', encoding='utf-8')
    loaded = load_config(config)
    assert loaded.mode == "replay"
    assert loaded.contracts["m2609"].multiplier == 10
    assert loaded.pairs[0].pair_id == "m_pair"


def test_live_config_requires_credentials_from_environment(tmp_path: Path, monkeypatch):
    config = tmp_path / "afuture.toml"
    config.write_text('''
[system]
mode = "live"
initial_capital = 500000

[ctp]
environment = "test"
td_address = "tcp://td"
md_address = "tcp://md"
''', encoding='utf-8')
    for key in ["AFUTURE_CTP_USER", "AFUTURE_CTP_PASSWORD", "AFUTURE_CTP_BROKER", "AFUTURE_CTP_APP_ID", "AFUTURE_CTP_AUTH_CODE"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError):
        load_config(config)


def test_config_rejects_invalid_contract_spec(tmp_path: Path):
    config = tmp_path / "bad.toml"
    config.write_text('''
[system]
mode = "replay"
[[contracts]]
symbol = "m2609"
exchange = "DCE"
multiplier = 0
price_tick = 1
margin_rate_long = 1.2
margin_rate_short = 0.1
''', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(config)


def test_config_rejects_duplicate_contract_usage_across_pairs(tmp_path: Path):
    config = tmp_path / "bad.toml"
    config.write_text('''
[system]
mode = "replay"
[[contracts]]
symbol = "m2609"
exchange = "DCE"
multiplier = 10
price_tick = 1
margin_rate_long = 0.1
margin_rate_short = 0.1
[[contracts]]
symbol = "m2701"
exchange = "DCE"
multiplier = 10
price_tick = 1
margin_rate_long = 0.1
margin_rate_short = 0.1
[[contracts]]
symbol = "m2705"
exchange = "DCE"
multiplier = 10
price_tick = 1
margin_rate_long = 0.1
margin_rate_short = 0.1
[[pairs]]
pair_id = "p1"
near_symbol = "m2609"
far_symbol = "m2701"
exchange = "DCE"
volume = 1
[[pairs]]
pair_id = "p2"
near_symbol = "m2701"
far_symbol = "m2705"
exchange = "DCE"
volume = 1
''', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(config)


def test_live_config_requires_expiry_dates_for_each_pair(tmp_path: Path, monkeypatch):
    config = tmp_path / "live.toml"
    config.write_text('''
[system]
mode = "live"
initial_capital = 500000
[ctp]
environment = "test"
td_address = "tcp://td"
md_address = "tcp://md"
[[contracts]]
symbol = "m2609"
exchange = "DCE"
multiplier = 10
price_tick = 1
margin_rate_long = 0.1
margin_rate_short = 0.1
[[contracts]]
symbol = "m2701"
exchange = "DCE"
multiplier = 10
price_tick = 1
margin_rate_long = 0.1
margin_rate_short = 0.1
[[pairs]]
pair_id = "p1"
near_symbol = "m2609"
far_symbol = "m2701"
exchange = "DCE"
volume = 1
''', encoding="utf-8")
    monkeypatch.setenv("AFUTURE_CTP_USER", "u")
    monkeypatch.setenv("AFUTURE_CTP_PASSWORD", "p")
    monkeypatch.setenv("AFUTURE_CTP_BROKER", "b")
    monkeypatch.delenv("AFUTURE_CTP_APP_ID", raising=False)
    monkeypatch.delenv("AFUTURE_CTP_AUTH_CODE", raising=False)
    with pytest.raises(ValueError, match="expiry"):
        load_config(config)
