# Dip Buyer — cbBTC 1-minute bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
[![make.fun](https://img.shields.io/badge/make.fun-btc--race--24h-blueviolet.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)

A two-layer dip-buying bot for the make.fun **Race a BTC trading bot live
for 24 hours** competition (`btc-race-24h-20261118-1444`). It trades Coinbase
Wrapped BTC (cbBTC) against USDC on one-minute bars, long only, no leverage,
speaking the `agent-fun/bars-replay/v1` protocol (one `hello`, one `batch` per
minute, one `reply` per batch).

## Strategy

- **Layer 1 — dip buy in an uptrend.** Buys when the close is at least
  60 bps below the trailing 48-bar high while still above its 240-bar EMA.
  Spends up to 100% of equity, takes profit at +90 bps from the fill price,
  cuts the loss at −60 bps, and never holds longer than 240 bars.
- **Layer 2 — crash catcher.** On a much deeper dip (250 bps below the
  trailing 48-bar high) it buys with whatever cash is left, with no regime
  filter, a wider take profit (+120 bps) and a wider stop (−120 bps).

At most one open lot per layer; exits are checked before entries, and a batch
never carries both a sell and a buy. The bot is fully deterministic: integer
arithmetic only, no wall clock, no network, no files outside the repository.

## Local dev-window result

Replayed on the kit's published dev window (1,440 one-minute bars,
2026-11-17 14:45 UTC → 2026-11-18 14:44 UTC) with the harness's exact fill
model (4 bps fee, 2 bps slippage, constant-product impact):

| Metric | Value |
|---|---|
| Net PnL | **+$18.76 USDC** |
| Return | +1.88% |
| Trades | 4 (2 round trips) |
| Max drawdown | 0.28% |

For comparison on the same window: buy-and-hold −$31.63, ema-cross −$148.17,
hold-cash $0.00.

## Layout

- `main.py` — the bot (Python 3.13, standard library only)
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python SDK shim
- `LICENSE` — MIT
