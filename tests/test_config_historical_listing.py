from pathlib import Path

from afuture.config import load_config


def test_replay_contract_catalog_preserves_listing_for_point_in_time_universe(tmp_path: Path):
    config_path = tmp_path / "replay.toml"
    config_path.write_text(
        """
[system]
mode = "replay"
initial_capital = 500000

[auto]
enabled = true
products = ["m"]
exchanges = ["DCE"]
max_contracts_per_product = 2
min_days_to_expiry = 0
session_windows = ["09:00-15:00"]

[[contracts]]
symbol = "m2609"
exchange = "DCE"
product = "m"
listing = "2025-09-15"
expiry = "2026-09-15"
multiplier = 10
price_tick = 1
margin_rate_long = 0.1
margin_rate_short = 0.1

[[contracts]]
symbol = "m2701"
exchange = "DCE"
product = "m"
listing = "2026-01-15"
expiry = "2027-01-15"
multiplier = 10
price_tick = 1
margin_rate_long = 0.1
margin_rate_short = 0.1
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert [row.listing for row in config.contract_catalog] == ["2025-09-15", "2026-01-15"]
