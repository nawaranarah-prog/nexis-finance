"""Cost-basis engine (FIFO or average cost — one method per calculation, never mixed).

Conventions
-----------
* Buys add a lot at ``quantity × price + fees`` (fees are capitalised into cost).
* Sells realise ``quantity × price − fees − cost of the shares removed``.
  FIFO removes the oldest lots first; average cost removes shares at the running average cost.
* ``transfer_in`` adds shares at the transaction price if given, otherwise at zero cost (flagged);
  ``transfer_out`` removes shares like a sell but realises nothing.
* ``split`` rows carry the split *ratio* in ``quantity`` (e.g. 4 for a 4-for-1 split):
  share counts multiply, per-share cost divides, total cost is unchanged.
* Dividends and interest are income, reported separately from realised trading P&L.
* A sell larger than the shares held is **not** silently accepted: the excess is reported as an
  ``oversell`` issue and ignored for P&L (short positions are out of scope).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

Method = Literal["fifo", "average"]
METHODS = ("fifo", "average")
_EPS = 1e-9


@dataclass
class Lot:
    quantity: float
    cost_per_share: float
    acquired: date


@dataclass
class Position:
    symbol: str
    lots: deque[Lot] = field(default_factory=deque)
    avg_qty: float = 0.0
    avg_cost_total: float = 0.0
    realized: float = 0.0
    income: float = 0.0
    fees: float = 0.0

    def quantity(self, method: Method) -> float:
        return sum(lot.quantity for lot in self.lots) if method == "fifo" else self.avg_qty

    def cost_total(self, method: Method) -> float:
        return sum(lot.quantity * lot.cost_per_share for lot in self.lots) if method == "fifo" else self.avg_cost_total


def compute(
    transactions: list[dict[str, Any]], method: Method = "fifo", prices: dict[str, float] | None = None
) -> dict[str, Any]:
    """``transactions``: dicts with trade_date, symbol, tx_type, quantity, price, fees, amount, code."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    prices = prices or {}
    pos: dict[str, Position] = {}
    issues: list[dict[str, Any]] = []
    realized_events: list[dict[str, Any]] = []
    cash_flows = defaultdict(float)
    ordered = sorted(transactions, key=lambda t: (t["trade_date"], t.get("code") or ""))
    for t in ordered:
        typ, sym = t["tx_type"], t.get("symbol")
        qty = abs(t.get("quantity") or 0.0)
        px = t.get("price")
        fees = abs(t.get("fees") or 0.0)
        if typ in ("deposit", "withdrawal"):
            cash_flows[typ] += abs(t.get("amount") or 0.0)
            continue
        if not sym:
            if typ == "fee":
                cash_flows["fees"] += fees or abs(t.get("amount") or 0.0)
            continue
        p = pos.setdefault(sym, Position(sym))
        if typ in ("buy", "transfer_in"):
            if qty <= _EPS:
                continue
            if px is None:
                if typ == "buy":
                    issues.append(
                        {
                            "code": t.get("code"),
                            "symbol": sym,
                            "issue": "buy_without_price",
                            "detail": "buy has no price; cost recorded as zero",
                        }
                    )
                else:
                    issues.append(
                        {
                            "code": t.get("code"),
                            "symbol": sym,
                            "issue": "transfer_zero_cost",
                            "detail": "transfer-in without a price is recorded at zero cost basis",
                        }
                    )
                px = 0.0
            cost = qty * px + fees
            p.fees += fees
            if method == "fifo":
                p.lots.append(Lot(qty, cost / qty, t["trade_date"]))
            else:
                p.avg_qty += qty
                p.avg_cost_total += cost
        elif typ in ("sell", "transfer_out"):
            held = p.quantity(method)
            if qty > held + _EPS:
                issues.append(
                    {
                        "code": t.get("code"),
                        "symbol": sym,
                        "issue": "oversell",
                        "detail": f"sells {qty:g} but only {held:g} held; excess ignored",
                    }
                )
                qty = held
            if qty <= _EPS:
                continue
            removed_cost = 0.0
            if method == "fifo":
                remaining = qty
                while remaining > _EPS and p.lots:
                    lot = p.lots[0]
                    take = min(lot.quantity, remaining)
                    removed_cost += take * lot.cost_per_share
                    lot.quantity -= take
                    remaining -= take
                    if lot.quantity <= _EPS:
                        p.lots.popleft()
            else:
                avg = p.avg_cost_total / p.avg_qty if p.avg_qty > _EPS else 0.0
                removed_cost = avg * qty
                p.avg_qty -= qty
                p.avg_cost_total -= removed_cost
                if p.avg_qty <= _EPS:
                    p.avg_qty, p.avg_cost_total = 0.0, 0.0
            p.fees += fees
            if typ == "sell":
                proceeds = qty * (px or 0.0) - fees
                gain = proceeds - removed_cost
                p.realized += gain
                realized_events.append(
                    {
                        "code": t.get("code"),
                        "date": t["trade_date"].isoformat(),
                        "symbol": sym,
                        "quantity": qty,
                        "proceeds": proceeds,
                        "cost": removed_cost,
                        "realized": gain,
                    }
                )
        elif typ == "split":
            ratio = qty
            if ratio <= _EPS:
                issues.append({"code": t.get("code"), "symbol": sym, "issue": "invalid_split", "detail": "split ratio missing"})
                continue
            if method == "fifo":
                for lot in p.lots:
                    lot.quantity *= ratio
                    lot.cost_per_share /= ratio
            else:
                p.avg_qty *= ratio
        elif typ in ("dividend", "interest"):
            p.income += abs(t.get("amount") or 0.0) or (qty * (px or 0.0))
        elif typ == "fee":
            p.fees += fees or abs(t.get("amount") or 0.0)

    positions = []
    for sym, p in sorted(pos.items()):
        q = p.quantity(method)
        cost = p.cost_total(method)
        mv = q * prices[sym] if sym in prices and q > _EPS else None
        positions.append(
            {
                "symbol": sym,
                "quantity": q if q > _EPS else 0.0,
                "cost_basis": cost if q > _EPS else 0.0,
                "average_cost": cost / q if q > _EPS else None,
                "market_price": prices.get(sym),
                "market_value": mv,
                "unrealized_pnl": (mv - cost) if mv is not None else None,
                "realized_pnl": p.realized,
                "income": p.income,
                "fees": p.fees,
                "open_lots": [
                    {"quantity": lot.quantity, "cost_per_share": lot.cost_per_share, "acquired": lot.acquired.isoformat()}
                    for lot in p.lots
                ]
                if method == "fifo"
                else None,
            }
        )
    return {
        "method": method,
        "positions": positions,
        "realized_events": realized_events,
        "totals": {
            "realized_pnl": sum(p["realized_pnl"] for p in positions),
            "unrealized_pnl": sum(p["unrealized_pnl"] for p in positions if p["unrealized_pnl"] is not None),
            "income": sum(p["income"] for p in positions),
            "fees": sum(p["fees"] for p in positions) + cash_flows.get("fees", 0.0),
            "deposits": cash_flows.get("deposit", 0.0),
            "withdrawals": cash_flows.get("withdrawal", 0.0),
            "unpriced_positions": [p["symbol"] for p in positions if p["quantity"] > 0 and p["market_price"] is None],
        },
        "issues": issues,
    }


def quantity_path(transactions: list[dict[str, Any]]) -> dict[str, list[tuple[date, float]]]:
    """Cumulative share count per symbol after each dated transaction (method-independent)."""
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    q: dict[str, float] = defaultdict(float)
    for t in sorted(transactions, key=lambda t: (t["trade_date"], t.get("code") or "")):
        sym = t.get("symbol")
        if not sym:
            continue
        qty = abs(t.get("quantity") or 0.0)
        typ = t["tx_type"]
        if typ in ("buy", "transfer_in"):
            q[sym] += qty
        elif typ in ("sell", "transfer_out"):
            q[sym] = max(0.0, q[sym] - qty)
        elif typ == "split" and qty > 0:
            q[sym] *= qty
        else:
            continue
        out[sym].append((t["trade_date"], q[sym]))
    return dict(out)
