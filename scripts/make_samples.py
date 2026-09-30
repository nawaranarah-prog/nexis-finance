"""Write the sample brokerage statements used by "Load sample files" (data/samples/).

These files are FICTIONAL: real ticker symbols, invented quantities, trade dates and trade prices.
They exist so the import → normalisation → reconstruction → reconciliation workflow can be
demonstrated without anyone's personal data. The formats deliberately differ (CSV with a preamble,
XLSX with unusual headers, nested JSON) to exercise automatic column mapping.

A deliberate inconsistency is included: Brokerage A's snapshot lists 100 MSFT while its transaction
history implies 95, which the reconciliation view should surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parents[1] / "data" / "samples"
OUT.mkdir(parents=True, exist_ok=True)

holdings_a = """Positions for account Individual ...1001 as of 09/25/2026 (SAMPLE FILE - fictional quantities)
Symbol,Description,Quantity,Price,Market Value,Average Cost,Security Type,Account
AAPL,APPLE INC,40,,,146.10,Equity,BRK-A-1001
MSFT,MICROSOFT CORP,100,,,300.40,Equity,BRK-A-1001
NVDA,NVIDIA CORP,60,,,125.05,Equity,BRK-A-1001
SPY,SPDR S&P 500 ETF,30,,,420.03,ETF,BRK-A-1001
TLT,ISHARES 20+ YEAR TREASURY BOND ETF,50,,,95.02,ETF,BRK-A-1001
JNJ,JOHNSON & JOHNSON,35,,,160.03,Equity,BRK-A-1001
CASH & CASH INVESTMENTS,Cash sweep,,,"$4,210.55",,Cash and Money Market,BRK-A-1001
Account Total,,,,,,,BRK-A-1001
"""

transactions_a = """Trade Date,Action,Symbol,Quantity,Price,Fees & Comm,Amount,Account
03/01/2022,Deposit,,,,,"100,000.00",BRK-A-1001
03/15/2022,Buy,AAPL,50,150.00,1.00,"-7,501.00",BRK-A-1001
06/10/2022,Buy,MSFT,60,280.00,1.00,"-16,801.00",BRK-A-1001
09/01/2022,Buy,JNJ,35,160.00,1.00,"-5,601.00",BRK-A-1001
01/12/2023,Buy,SPY,30,420.00,1.00,"-12,601.00",BRK-A-1001
03/01/2023,Buy,TLT,50,95.00,1.00,"-4,751.00",BRK-A-1001
05/02/2023,Buy,MSFT,35,330.00,1.00,"-11,551.00",BRK-A-1001
02/20/2024,Sell,AAPL,10,190.00,1.00,"1,899.00",BRK-A-1001
05/16/2024,Qualified Dividend,AAPL,,,,9.60,BRK-A-1001
08/05/2024,Buy,NVDA,40,120.00,1.00,"-4,801.00",BRK-A-1001
01/10/2025,Buy,NVDA,20,135.00,1.00,"-2,701.00",BRK-A-1001
03/31/2025,Cash Dividend,SPY,,,,51.30,BRK-A-1001
06/30/2025,Service Fee,,,,25.00,-25.00,BRK-A-1001
"""

(OUT / "brokerage_a_holdings.csv").write_text(holdings_a, encoding="utf-8")
(OUT / "brokerage_a_transactions.csv").write_text(transactions_a, encoding="utf-8")

wb = Workbook()
ws = wb.active
ws.title = "Positions"
ws.append(["Acct No", "Ticker", "Security Name", "Shares", "Avg Cost", "Last", "CCY"])
for row in [
    ["B-7788", "GOOGL", "Alphabet Inc. Class A", 25, 118.40, None, "USD"],
    ["B-7788", "AMZN", "Amazon.com Inc.", 30, 131.25, None, "USD"],
    ["B-7788", "JPM", "JPMorgan Chase & Co.", 40, 142.10, None, "USD"],
    ["B-7788", "XOM", "Exxon Mobil Corp.", 45, 101.80, None, "USD"],
    ["B-7788", "BRK.B", "Berkshire Hathaway Inc. Class B", 10, 355.00, None, "USD"],
    [
        "B-7788",
        "ASML.AS",
        "ASML Holding N.V. (Euronext Amsterdam)",
        5,
        610.00,
        None,
        "EUR",
    ],
]:
    ws.append(row)
wb.save(OUT / "brokerage_b_positions.xlsx")

retirement = {
    "statement": "SAMPLE retirement plan statement — fictional quantities",
    "as_of": "2026-09-24",
    "positions": [
        {
            "security": "VTI",
            "units": 80,
            "unit_cost": 205.10,
            "asset_class": "ETF",
            "currency": "USD",
        },
        {
            "security": "BND",
            "units": 120,
            "unit_cost": 72.40,
            "asset_class": "ETF",
            "currency": "USD",
        },
        {
            "security": "VXUS",
            "units": 90,
            "unit_cost": 55.80,
            "asset_class": "ETF",
            "currency": "USD",
        },
        {
            "security": "CASH",
            "units": 1500.00,
            "unit_cost": 1.0,
            "asset_class": "Money market",
            "currency": "USD",
        },
    ],
}
(OUT / "retirement_account.json").write_text(json.dumps(retirement, indent=2), encoding="utf-8")
print(f"wrote samples to {OUT}")
