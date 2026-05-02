#!/usr/bin/env python3
"""
fed-trading params_writer
每日收盘后读取 signal_outcomes，用EMA慢速更新参数，版本化回写
"""
import psycopg2, yaml, os, logging
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PARAMS] %(message)s")
log = logging.getLogger(__name__)

DB       = "host=192.168.0.11 port=5432 dbname=quantforce user=heng password=newpassword123"
PARAMS_DIR = Path("/home/heng/quantforce-apex/config/params")
LATEST     = PARAMS_DIR / "params_latest.yaml"

EMA_WEIGHT  = 0.1   # 慢速更新
MIN_SAMPLES = 30    # 冷启动保护
MAX_ADJUST  = 0.10  # 单次最大调整10%
MIN_IMPROVE = 0.03  # 最小胜率提升3%

TUNABLE = {
    "strategy_A": {
        "rvol_min":          (1.2, 2.5),
        "bb_squeeze_ratio":  (0.60, 0.90),
        "price_range_pct":   (0.05, 0.12),
    },
    "strategy_B": {
        "rvol_min":          (1.2, 2.5),
        "bb_squeeze_ratio":  (0.60, 0.90),
        "price_range_pct":   (0.05, 0.12),
    }
}

def load_outcomes(strategy):
    conn = psycopg2.connect(DB)
    cur  = conn.cursor()
    cur.execute("""
        SELECT rvol, bb_squeeze_days, groq_score, pnl_pct
        FROM signal_outcomes
        WHERE strategy=%s AND exit_time IS NOT NULL
          AND created_at > NOW() - INTERVAL '90 days'
    """, (strategy,))
    rows = cur.fetchall()
    conn.close()
    return rows

def compute_suggestions(rows, strategy):
    if len(rows) < MIN_SAMPLES:
        log.warning(f"{strategy}: only {len(rows)} samples, need {MIN_SAMPLES}. Skipping.")
        return None

    profitable = [r for r in rows if r[3] and r[3] > 0]
    win_rate   = len(profitable) / len(rows)
    log.info(f"{strategy}: {len(rows)} trades, win rate {win_rate*100:.1f}%")

    if not profitable:
        return None

    # 从盈利样本提取最优参数
    avg_rvol = sum(r[0] for r in profitable if r[0]) / len(profitable)
    suggestions = {
        "rvol_min": round(avg_rvol * 0.9, 2),  # 略低于盈利均值
    }
    return suggestions, win_rate

def ema_update(current, suggested, bounds):
    lo, hi = bounds
    new_val = current * (1 - EMA_WEIGHT) + suggested * EMA_WEIGHT
    # 限制调整幅度
    max_change = current * MAX_ADJUST
    new_val = max(current - max_change, min(current + max_change, new_val))
    # 限制在可调范围内
    new_val = max(lo, min(hi, new_val))
    return round(new_val, 3)

def write_params(cfg, today_str):
    out_path = PARAMS_DIR / f"params_{today_str}.yaml"
    cfg["meta"] = {
        "version": today_str,
        "updated_by": "fed-trading params_writer",
        "updated_at": datetime.now().isoformat(),
    }
    with open(out_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    # 更新 symlink
    if LATEST.is_symlink():
        LATEST.unlink()
    LATEST.symlink_to(out_path)
    log.info(f"Written: {out_path}")
    log.info(f"Symlink updated: params_latest.yaml -> {out_path.name}")

def run():
    today = str(date.today())
    log.info(f"params_writer starting for {today}")

    # 读取当前参数
    cfg = yaml.safe_load(open(LATEST))
    updated = False

    for strategy in ["strategy_A", "strategy_B"]:
        rows = load_outcomes(strategy)
        if len(rows) < MIN_SAMPLES:
            log.warning(f"{strategy}: {len(rows)}/{MIN_SAMPLES} samples, skipping")
            continue

        result = compute_suggestions(rows, strategy)
        if not result:
            continue
        suggestions, win_rate = result

        strat_cfg = cfg.get(strategy, {})
        tunable   = TUNABLE.get(strategy, {})

        for param, suggested_val in suggestions.items():
            if param not in tunable:
                continue
            current = strat_cfg.get(param, suggested_val)
            bounds  = tunable[param]
            new_val = ema_update(current, suggested_val, bounds)

            if abs(new_val - current) < 0.001:
                log.info(f"{strategy}.{param}: no change ({current})")
                continue

            log.info(f"{strategy}.{param}: {current} -> {new_val} (suggested:{suggested_val})")
            cfg[strategy][param] = new_val
            updated = True

    if updated:
        write_params(cfg, today)
        log.info("Params updated successfully")
    else:
        log.info("No updates needed")

if __name__ == "__main__":
    run()
