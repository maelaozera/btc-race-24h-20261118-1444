"""Dip-buying bot for the make.fun btc-race-24h-20261118-1444 competition.

The bot trades Coinbase Wrapped BTC (cbBTC) against USDC on one-minute bars,
speaking the agent-fun/bars-replay/v1 protocol: it reads one hello message,
then one batch per minute, and answers every batch with exactly one reply
echoing its seq and carrying its orders. Orders decided on a batch fill at
the next bar's open, so every decision here uses only bars already closed.

Strategy (two-layer dip buying, long only, no leverage):
  * Layer 1 buys a dip: the close at least 60 bps below the trailing 48-bar
    high while the close is still above its 240-bar exponential average
    (an uptrend regime filter). It spends up to 100% of equity, takes
    profit at +90 bps from its fill price, cuts the loss at -60 bps, and
    never holds longer than 240 bars.
  * Layer 2 is a crash catcher: on a much deeper dip (250 bps below the
    trailing 48-bar high) it buys with whatever cash is left, no regime
    filter, with a wider take profit (+120 bps) and a wider stop (-120 bps).
  * At most one open lot per layer. Exits are checked before entries, and
    a batch never carries both a sell and a buy.

The bot is fully deterministic: integer arithmetic only, no wall clock, no
network, no files outside the repository, and any randomness would be seeded
from hello.seed (none is used).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
BPS = 10_000
MIN_NOTIONAL = 1_000_000  # 1 USDC, in micro-USDC
MAX_SLIPPAGE_BPS = 500

# Layer 1: dip buy in an uptrend.
HIGH_N = 48
EMA_N = 240
DIP1_BPS = 60
TP1_BPS = 90
SL1_BPS = 60
HOLD1_BARS = 240
FRAC1_BPS = 10_000  # share of equity layer 1 may spend

# Layer 2: crash catcher, no regime filter, wider targets.
DIP2_BPS = 250
TP2_BPS = 120
SL2_BPS = 120
HOLD2_BARS = 240


class Lot:
    """One open buy: its filled quantity, fill price, layer and fill bar."""

    __slots__ = ("qty", "entry", "layer", "bar")

    def __init__(self, qty, entry, layer, bar):
        self.qty = qty
        self.entry = entry
        self.layer = layer
        self.bar = bar


class Bot:
    """Dip-buying state: closes, the long EMA, and the open lots."""

    def __init__(self):
        self.closes = []
        self.ema = None
        self.lots = []
        self.next_id = 1
        self.buy_layer = {}  # client_id -> layer of a buy awaiting its fill
        self.sell_lots = {}  # client_id -> lots a sell will close

    def _client_id(self, prefix):
        cid = "%s%d" % (prefix, self.next_id)
        self.next_id += 1
        return cid

    def _apply_fills(self, state, batch):
        """Fold this batch's fills into the lots (buys open, sells close)."""
        for fill in batch.get("fills", []):
            if fill.get("status") != "filled":
                continue
            cid = fill.get("client_id")
            if fill.get("side") == "buy":
                layer = self.buy_layer.pop(cid, None)
                if layer is None:
                    continue
                self.lots.append(Lot(
                    qty=int(fill["qty"]),
                    entry=int(fill["price"]),
                    layer=layer,
                    bar=state.seq,
                ))
            else:
                remaining = int(fill["qty"])
                for lot in self.sell_lots.pop(cid, []):
                    if remaining <= 0:
                        break
                    if lot.qty <= remaining:
                        remaining -= lot.qty
                        if lot in self.lots:
                            self.lots.remove(lot)
                    else:
                        lot.qty -= remaining
                        remaining = 0

    def _update_indicators(self, close):
        """Append a close and advance the long EMA (integer arithmetic)."""
        self.closes.append(close)
        if self.ema is None:
            self.ema = close
        else:
            self.ema += (close - self.ema) * 2 // (EMA_N + 1)

    def on_batch(self, state, batch):
        """Decide this batch's orders (at most one) and return them."""
        if state.warming_up:
            return []
        self._apply_fills(state, batch)
        bar = state.latest.get(SYMBOL)
        if bar is None:
            return []
        close = int(bar["c"])
        self._update_indicators(close)
        # Exits first: take profit, stop loss or time stop per lot.
        exiting = []
        for lot in self.lots:
            if lot.layer == 1:
                tp, sl, hold = TP1_BPS, SL1_BPS, HOLD1_BARS
            else:
                tp, sl, hold = TP2_BPS, SL2_BPS, HOLD2_BARS
            hit_tp = close * BPS >= lot.entry * (BPS + tp)
            hit_sl = close * BPS <= lot.entry * (BPS - sl)
            hit_hold = state.seq - lot.bar >= hold
            if hit_tp or hit_sl or hit_hold:
                exiting.append(lot)
        if exiting:
            qty = sum(lot.qty for lot in exiting)
            held = state.positions.get(SYMBOL)
            if held is not None and qty > held.qty:
                qty = held.qty
            if qty <= 0:
                return []
            cid = self._client_id("s")
            self.sell_lots[cid] = exiting
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS,
                     "client_id": cid}]
        # Entries: layer 1 needs the uptrend regime, layer 2 needs only the
        # deep dip. One lot per layer at a time.
        high = max(self.closes[-HIGH_N:])
        dip_bps = (high - close) * BPS // high
        has_layer1 = any(lot.layer == 1 for lot in self.lots)
        has_layer2 = any(lot.layer == 2 for lot in self.lots)
        if not has_layer1 and close >= self.ema and dip_bps >= DIP1_BPS:
            # Spend up to FRAC1 of equity, never more than the cash held
            # and never more than the weight cap leaves room for.
            spend = min(state.equity * FRAC1_BPS // BPS, state.cash,
                        state.weight_room(SYMBOL))
            if spend >= MIN_NOTIONAL:
                cid = self._client_id("b")
                self.buy_layer[cid] = 1
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": spend,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS,
                         "client_id": cid}]
        elif not has_layer2 and dip_bps >= DIP2_BPS:
            spend = state.cash
            if spend >= MIN_NOTIONAL:
                cid = self._client_id("b")
                self.buy_layer[cid] = 2
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": spend,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS,
                         "client_id": cid}]
        return []


def main():
    bot = Bot()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
