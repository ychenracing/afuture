"""临时输出修正后两年研究的完整经济诊断，不参与参数或品种选择。

该脚本复用 ``evaluate_daily_relative_strategy`` 的固定参数、资格规则和生产一致性
数据处理，只把旧脚本在品种身份断言之前已经算出的结果完整落盘，便于区分
“旧品种名变化”与“经济门真正失效”。最终发布前删除本文件。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import evaluate_daily_relative_strategy as core


def _annualized(total_return: float, trading_days: int) -> float:
    if trading_days <= 0 or total_return <= -1.0:
        return -1.0 if total_return <= -1.0 else 0.0
    return (1.0 + float(total_return)) ** (252.0 / trading_days) - 1.0


def main() -> None:
    runtime = Path("runtime")
    current = pd.read_csv(runtime / "two_year_broad_60m.csv")
    prior = pd.read_csv(runtime / "prior_two_year_broad_60m.csv")
    frames = core.build_pair_frames(prior, current)

    qualified_prior = core.qualify(frames, core.PROFILE, "prior1", "prior2")
    qualified_current = core.qualify(frames, core.PROFILE, "train", "validation")
    prior_forward = core.metrics(core.portfolio(frames, qualified_prior, "train", core.PROFILE))
    final_oos = core.metrics(core.portfolio(frames, qualified_current, "oos", core.PROFILE))
    full_recent_trades = core.portfolio(frames, qualified_current, "full_recent", core.PROFILE)
    full_recent = core.metrics(full_recent_trades)

    neighbors = []
    for label, params in core.neighbor_profiles():
        prior_products = core.qualify(frames, params, "prior1", "prior2")
        current_products = core.qualify(frames, params, "train", "validation")
        prior_metrics = core.metrics(core.portfolio(frames, prior_products, "train", params))
        oos_metrics = core.metrics(core.portfolio(frames, current_products, "oos", params))
        passed = bool(
            prior_products
            and current_products
            and prior_metrics["trades"] >= 2
            and oos_metrics["trades"] >= 2
            and prior_metrics["R"] > 0
            and oos_metrics["R"] > 0
        )
        neighbors.append(
            {
                "variant": label,
                "prior_products": prior_products,
                "current_products": current_products,
                "prior_forward": prior_metrics,
                "oos": oos_metrics,
                "passed": passed,
            }
        )

    capital_proxy = {
        name: core.capital_proxy(full_recent_trades, risk)
        for name, risk in (("1pct_risk", 0.01), ("1_5pct_risk", 0.015), ("2pct_risk", 0.02))
    }
    for row in capital_proxy.values():
        row["annualized_return"] = _annualized(row["return"], 484)

    report = {
        "qualified_prior": qualified_prior,
        "qualified_current": qualified_current,
        "prior_forward": prior_forward,
        "final_oos": final_oos,
        "full_recent": full_recent,
        "capital_proxy": capital_proxy,
        "neighbor_pass_count": sum(item["passed"] for item in neighbors),
        "neighbor_total": len(neighbors),
        "neighbor_pass_ratio": (
            sum(item["passed"] for item in neighbors) / len(neighbors)
            if neighbors
            else 0.0
        ),
        "neighbors": neighbors,
        "annualized_target": 1.0,
        "target_met_at_2pct_risk_proxy": capital_proxy["2pct_risk"]["annualized_return"] >= 1.0,
        "note": "diagnostic only; product identity is selection output, not an acceptance input",
    }
    path = runtime / "two_year_diagnostic.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
