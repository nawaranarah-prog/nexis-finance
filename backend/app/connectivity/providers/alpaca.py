"""Alpaca brokerage (Trading API v2) via user-issued API key pair.

Alpaca issues API keys (not passwords) from its dashboard, and supports free paper accounts, so
a user can connect without ever giving Nexis a brokerage password. Keys are encrypted at rest and
only used server-side. This adapter is implemented against Alpaca's documented response schema and
verified with mocked responses in the test suite; it has not been exercised against a live
account in this build, which the marketplace states explicitly.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.connectivity.base import BrokerageProvider, CredentialField, ProviderSpec
from app.core.errors import ConfigurationError, ProviderError


class AlpacaProvider(BrokerageProvider):
    spec = ProviderSpec(
        key="alpaca",
        name="Alpaca",
        category="accounts",
        description="US stock/ETF brokerage with an official Trading API. Paper-trading accounts are free.",
        capabilities=("accounts", "holdings", "transactions", "balances"),
        auth_type="api_key",
        requirements=("Alpaca account (paper or live)", "API key ID and secret from the Alpaca dashboard"),
        docs_url="https://docs.alpaca.markets/reference/getaccount-1",
        implemented=True,
        verification="mocked",
        data_class="real_external",
        credential_fields=(
            CredentialField("key_id", "API key ID", secret=False),
            CredentialField("secret_key", "API secret key", secret=True),
        ),
        config_fields=(
            {"name": "environment", "label": "Environment", "type": "choice", "choices": ["paper", "live"], "default": "paper"},
        ),
        notes="Implemented against Alpaca's documented API and verified with mocked responses; not yet exercised against a live account in this build.",
    )

    @property
    def base(self) -> str:
        return "https://api.alpaca.markets" if self.config.get("environment") == "live" else "https://paper-api.alpaca.markets"

    def _headers(self) -> dict[str, str]:
        kid, sec = self.credentials.get("key_id"), self.credentials.get("secret_key")
        if not kid or not sec:
            raise ConfigurationError("Alpaca requires an API key ID and secret")
        return {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.http_get(f"{self.base}{path}", params=params, headers=self._headers())
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError("Alpaca: invalid JSON response") from exc

    def verify(self) -> dict[str, Any]:
        acct = self._get("/v2/account")
        if "account_number" not in acct:
            raise ProviderError("Alpaca: unexpected account response")
        return {"account_number": acct["account_number"][-4:], "status": acct.get("status")}

    def get_accounts(self) -> list[dict[str, Any]]:
        a = self._get("/v2/account")
        return [
            {
                "external_id": a["account_number"],
                "name": f"Alpaca {self.config.get('environment', 'paper')} ••{a['account_number'][-4:]}",
                "institution": "Alpaca",
                "account_type": "brokerage",
                "base_currency": a.get("currency", "USD"),
            }
        ]

    def get_balances(self, account_external_id: str) -> dict[str, Any]:
        a = self._get("/v2/account")
        return {"cash": float(a.get("cash", 0)), "equity": float(a.get("equity", 0)), "currency": a.get("currency", "USD")}

    def get_holdings(self, account_external_id: str) -> list[dict[str, Any]]:
        rows = []
        for p in self._get("/v2/positions"):
            rows.append(
                {
                    "symbol": p["symbol"],
                    "quantity": float(p["qty"]),
                    "average_cost": float(p["avg_entry_price"]),
                    "price": float(p["current_price"]) if p.get("current_price") else None,
                    "market_value": float(p["market_value"]) if p.get("market_value") else None,
                    "currency": "USD",
                    "asset_class": "equity" if p.get("asset_class") == "us_equity" else p.get("asset_class", "unknown"),
                    "raw": p,
                }
            )
        bal = self.get_balances(account_external_id)
        if bal.get("cash"):
            rows.append(
                {
                    "symbol": "CASH",
                    "quantity": bal["cash"],
                    "average_cost": 1.0,
                    "price": 1.0,
                    "market_value": bal["cash"],
                    "currency": bal["currency"],
                    "asset_class": "cash",
                    "raw": {"source": "account.cash"},
                }
            )
        return rows

    def get_transactions(self, account_external_id: str, since: date | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "activity_types": "FILL,DIV,DIVCGL,DIVNRA,INT,FEE,CSD,CSW,SPLIT",
            "direction": "asc",
            "page_size": 100,
        }
        if since:
            params["after"] = since.isoformat()
        for _ in range(50):  # pagination guard
            page = self._get("/v2/account/activities", params)
            if not page:
                break
            for a in page:
                t = a.get("activity_type")
                d = pd.Timestamp(a.get("transaction_time") or a.get("date")).date()
                if t == "FILL":
                    side = "buy" if a.get("side") == "buy" else "sell"
                    out.append(
                        {
                            "date": d,
                            "symbol": a["symbol"],
                            "type": side,
                            "quantity": float(a["qty"]),
                            "price": float(a["price"]),
                            "fees": 0.0,
                            "amount": None,
                            "raw": a,
                        }
                    )
                elif t and t.startswith("DIV"):
                    out.append(
                        {
                            "date": d,
                            "symbol": a.get("symbol"),
                            "type": "dividend",
                            "quantity": None,
                            "price": None,
                            "fees": 0.0,
                            "amount": float(a.get("net_amount", 0)),
                            "raw": a,
                        }
                    )
                elif t in ("CSD", "CSW", "INT", "FEE"):
                    kind = {"CSD": "deposit", "CSW": "withdrawal", "INT": "interest", "FEE": "fee"}[t]
                    amt = abs(float(a.get("net_amount", 0)))
                    out.append(
                        {
                            "date": d,
                            "symbol": None,
                            "type": kind,
                            "quantity": None,
                            "price": None,
                            "fees": amt if kind == "fee" else 0.0,
                            "amount": amt,
                            "raw": a,
                        }
                    )
                elif t == "SPLIT":
                    out.append(
                        {
                            "date": d,
                            "symbol": a.get("symbol"),
                            "type": "split",
                            "quantity": float(a.get("qty") or 0),
                            "price": None,
                            "fees": 0.0,
                            "amount": None,
                            "raw": a,
                        }
                    )
            params["page_token"] = page[-1].get("id")
            if len(page) < params["page_size"]:
                break
        return out
