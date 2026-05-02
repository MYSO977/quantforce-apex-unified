# ⚡ QuantForce Apex + fed-trading

**Unified algorithmic trading infrastructure — dual account, self-evolving parameters, 5-node LAN cluster**

> Manual execution | Home-based | Winnipeg, MB | Live since 2026-04-10

---

## 📊 System Status

| Component | Status | Last Updated |
|-----------|--------|-------------|
| Signal pipeline | ✅ Live | 2026-05-02 |
| PostgreSQL (.11) | ✅ Running | NVMe wired |
| BB Squeeze scanner | 🔧 Deploying | 2026-05-02 |
| fed-trading autotuner | 🔧 Deploying | 2026-05-02 |
| IB executor | ❌ Disabled | Manual mode |

---

## 💼 Trading Accounts

### Strategy A — IB US Equities (USD)
- **Account:** $1,200 USD · IB Cash · Paper account DUP375010
- **Position size:** $400 fixed · Max 3 positions
- **Stop / Target:** -3% / +12% · RR 1:4
- **Hold:** 5-10 trading days
- **Universe:** Russell 2000 + ETF (~2,000 symbols)
- **Price range:** $3 - $20 USD

### Strategy B — BMO Canadian Equities (CAD)
- **Account:** C$18,000 CAD · BMO InvestorLine RESP
- **Position size:** C$2,000 fixed · Max 9 positions
- **Stop / Target:** -2% / +6% · RR 1:3
- **Hold:** 10-15 trading days
- **Universe:** TSX whitelist (94 symbols)

---

## 🔍 Signal Filter Chain

```
Whitelist
  └─ Price Range ($3-$20 USD, Strategy A only)
      └─ Consolidation: BB Squeeze (BB Width < 80% of 20-day avg)
          └─ Consolidation: N-day Range (10-day range < 8%)  [both required]
              └─ Volume/Price: RVOL > 1.5  AND  up day on up volume
                  └─ Technical Align: EMA9 > EMA20 > VWAP > Day Open
                      └─ [News Amplifier: Groq >= 7.5 → confidence x1.3]
                          └─ Signal → Email → Manual Order
```

> **Note:** News is NOT a gate filter. It is a confidence amplifier. Signals fire without news; when relevant news exists and scores ≥ 7.5, the signal confidence is multiplied by 1.3 for prioritization.

---

## 🧠 fed-trading Auto-Tuning

fed-trading is no longer a standalone trading system. It is the **parameter evolution engine** for quantforce-apex.

### How it works

```
apex generates signals
    ↓
User places manual orders (BMO / IB)
    ↓
Trade results recorded in signal_outcomes table
    ↓
fed-trading reads results daily at 16:30 ET
    ↓
Backtests last 30 days across tunable parameter space
    ↓
EMA slow-update: new = 0.9 × current + 0.1 × suggested
    ↓
Writes params_YYYY-MM-DD.yaml + updates params_latest.yaml
    ↓
apex reads new params on next startup → evolution complete
```

### Tunable parameters

| Parameter | Default | Tunable Range | Fixed? |
|-----------|---------|---------------|--------|
| `rvol_min` | 1.5 | [1.2, 2.5] | No |
| `bb_squeeze_ratio` | 0.80 | [0.60, 0.90] | No |
| `price_range_pct` | 0.08 | [0.05, 0.12] | No |
| `position_size` | $400 / C$2,000 | — | **Fixed** |
| `stop_loss` | -3% / -2% | — | **Fixed** |
| `take_profit` | +12% / +6% | — | **Fixed** |
| `hold_days` | [5,10] / [10,15] | — | **Fixed** |
| `news_boost` | 1.3 | — | **Fixed** |

### Cold-start protection
- Fewer than 30 rows in `signal_outcomes` → no update
- Single adjustment > 10% → rejected
- Win-rate improvement < 3% → no update
- Param outside tunable range → clamped to boundary

### Version control
```
config/params/
  params_2026-05-02.yaml   ← daily backup
  params_2026-05-03.yaml
  params_latest.yaml       ← symlink (apex reads this)
```
Rollback: `ln -sf params_2026-05-02.yaml params_latest.yaml`

---

## 🖥️ Node Architecture

| Node | LAN IP | Tailscale | Role | Hardware |
|------|--------|-----------|------|----------|
| .18 Acer (Brain) | 192.168.0.18 | 100.67.10.48 | Signal fusion · Dashboard | i7-4790 · 15.8GB · WiFi |
| .11 Dell (Core) | 192.168.0.11 | 100.97.5.9 | PostgreSQL · GPU · Autotuning | i5-8500 · 15.8GB · NVMe · GTX750 · **Wired** |
| .143 Lenovo (Scan) | 192.168.0.143 | 100.125.173.94 | Scanners · Email notify | i7-8550U · 7.6GB · NVMe · 940MX · WiFi |
| .101 Sentry | 192.168.0.101 | 100.66.120.42 | Monitoring | Asus L406M · WiFi |
| .102 Courier | 192.168.0.102 | 100.112.86.32 | Notifications | Asus L410M · WiFi |

### Service distribution

**.11 Dell** — computation core (wired, NVMe, GPU)
- PostgreSQL · gpu_indicator · BB Squeeze engine · auto-backtest · fed-trading tuner · open_webui

**.18 Acer** — coordination (WiFi)
- signal_fusion · market_feed_yf · tsx_scanner · panel_api · params_loader

**.143 Lenovo** — scanner cluster (WiFi, RAM limited)
- us_scanner ($3-$20) · cad_scanner · news_scanner · email_notifier

---

## 🗄️ Database Schema

```
universe_whitelist  →  market_events  →  signals_raw  →  signals_final  →  executions
                                                                              ↑
                                                                        signal_outcomes
                                                                        (fed-trading feedback)
```

| Table | Purpose | Rows |
|-------|---------|------|
| `universe_whitelist` | Stock universe (Russell2000 + TSX) | 2,198 |
| `market_events` | 5-min OHLCV | 69,530 |
| `signals_raw` | Raw scanner output | 4,849 |
| `signals_final` | Post L1-L4 filter | 32 |
| `executions` | Order records | 0 (manual mode) |
| `signal_outcomes` | Real trade results for LoRA training | deploying |

---

## 🚀 Quick Start

```bash
# SSH to nodes
ssh 192.168.0.18   # Brain
ssh 192.168.0.11   # Core (DB + GPU)
ssh 192.168.0.143  # Scanners

# Connect to database
PGPASSWORD=newpassword123 psql -h 192.168.0.11 -U heng -d quantforce

# Check latest signals
SELECT symbol, direction, score, created_at FROM signals_raw ORDER BY id DESC LIMIT 10;

# Dashboard (LAN)
http://192.168.0.18:5801/light

# Dashboard (Tailscale / remote)
http://100.67.10.48:5801/light
```

---

## 📈 Deployment Roadmap

- [x] PostgreSQL migrated to .11 (wired NVMe)
- [x] Tailscale VPN — all 5 nodes connected
- [x] signal_fusion + market_feed running
- [x] tsx_scanner + cad_scanner + news_scanner running
- [x] First confirmed paper trade: CLYM BUY filled 2026-04-10
- [ ] `params_latest.yaml` — dynamic param loading
- [ ] `signal_outcomes` table — trade feedback loop
- [ ] BB Squeeze consolidation detector
- [ ] Price range filter $3-$20 (us_scanner)
- [ ] News confidence amplifier ×1.3
- [ ] fed-trading backtest → EMA param update
- [ ] Versioned param write-back with rollback

---

## 🏗️ Repository Structure

```
quantforce-apex-unified/
├── apex/                  # quantforce-apex signal engine
│   ├── nodes/brain_18/   # signal_fusion, market_feed
│   ├── core/             # db, risk_gate, interfaces
│   ├── strategies/       # MomentumSwing, NewsBreakout, ORB, SectorRotation
│   └── config/params/    # params_latest.yaml + daily backups
├── fed-trading/           # parameter evolution engine
│   ├── src/              # train_local, params_writer, backtest
│   └── loras/            # LoRA weight files
├── scanners/              # us_scanner, cad_scanner, news_scanner
└── dashboard/             # panel_api + frontend
```

---

*QuantForce Labs — Winnipeg, MB — 2026*
