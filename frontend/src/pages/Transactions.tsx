import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, Field, Kpi, PageHead, QueryView } from "../components/ui";
import { MethodSelect, useAccounts } from "../components/connect";
import { DateRange } from "../components/pickers";
import { seriesColor } from "../components/charts";
import { useResolvedTheme } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { int, money, num, tone } from "../utils/format";

const TYPES = ["", "buy", "sell", "dividend", "interest", "fee", "deposit", "withdrawal", "split", "transfer_in", "transfer_out"];

export default function Transactions() {
  const theme = useResolvedTheme();
  const accounts = useAccounts();
  const [method, setMethod] = useState("fifo");
  const [f, setF] = useState({ account: "", symbol: "", type: "", start: "", end: "" });
  const q = useQuery({
    queryKey: ["intel", "tx", method, f],
    queryFn: () => api.get<AnyObj>("/intelligence/transactions", { method, account_id: f.account, symbol: f.symbol, tx_type: f.type, start: f.start, end: f.end }),
  });
  return (
    <>
      <PageHead title="Transaction Analytics" desc="Trading activity, fees and turnover from imported transactions, with a clearly stated cost-basis method for realised and unrealised P&L."
        actions={<MethodSelect value={method} onChange={setMethod} />} />
      <Card title="Filters">
        <div className="row" style={{ gap: 12, alignItems: "flex-end" }}>
          <Field label="Account"><select className="input" style={{ width: 220 }} value={f.account} onChange={(e) => setF({ ...f, account: e.target.value })}>
            <option value="">All accounts</option>{(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field label="Symbol"><input className="input" style={{ width: 110 }} value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value.toUpperCase() })} /></Field>
          <Field label="Type"><select className="input" style={{ width: 140 }} value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>{TYPES.map((t) => <option key={t} value={t}>{t || "all types"}</option>)}</select></Field>
          <Field label="Date range"><DateRange start={f.start} end={f.end} onChange={(s, e) => setF({ ...f, start: s, end: e })} /></Field>
        </div>
      </Card>
      <div style={{ marginTop: 12 }}>
        <QueryView q={q} label="Analysing transactions">
          {(t) => (
            <div className="stack">
              <div className="kpis">
                <Kpi label="Transactions" value={int(t.counts.transactions)} note={t.period[0] ? `${t.period[0]} → ${t.period[1]}` : undefined} />
                <Kpi label="Trades" value={int(t.counts.trades)} note={`${t.counts.buys} buys · ${t.counts.sells} sells`} />
                <Kpi label="Traded notional" value={money(t.traded_notional)} />
                <Kpi label="Fees" value={money(t.fees_total, 2)} />
                <Kpi label="Trading frequency" value={`${num(t.trades_per_month, 2)} / mo`} />
                <Kpi label={`Realised P&L (${method.toUpperCase()})`} value={<span className={tone(t.pnl.totals.realized_pnl)}>{money(t.pnl.totals.realized_pnl, 2)}</span>} />
                <Kpi label="Unrealised P&L" value={<span className={tone(t.pnl.totals.unrealized_pnl)}>{money(t.pnl.totals.unrealized_pnl, 2)}</span>} note="at latest stored price" />
                <Kpi label="Dividend & interest income" value={money(t.pnl.totals.income, 2)} />
              </div>
              <div className="banner neutral small"><b>Cost basis method: {method === "fifo" ? "FIFO" : "Average cost"}</b>. {t.methodology[method]} {t.methodology.common}
                {t.pnl.issues.length > 0 && <span className="neg"> {t.pnl.issues.length} issue(s): {t.pnl.issues.slice(0, 3).map((i: AnyObj) => `${i.symbol} ${i.issue}`).join(", ")}.</span>}</div>
              <Card title="Monthly activity">
                <Plot height={240} data={[
                  { type: "bar", name: "Buys", x: t.monthly.map((m: AnyObj) => m.month), y: t.monthly.map((m: AnyObj) => m.buys), marker: { color: seriesColor(theme, 0) } },
                  { type: "bar", name: "Sells", x: t.monthly.map((m: AnyObj) => m.month), y: t.monthly.map((m: AnyObj) => m.sells), marker: { color: seriesColor(theme, 1) } },
                ]} layout={{ barmode: "group", yaxis: { tickprefix: "$", tickformat: ",.2s" }, xaxis: { type: "category" } }} />
              </Card>
              <Card title="Positions and P&L by holding" flush>
                <DataTable<AnyObj> rows={t.pnl.positions} exportName={`pnl-${method}`} columns={[
                  { key: "account", label: "Account" }, { key: "symbol", label: "Symbol", render: (p) => <b className="mono">{p.symbol}</b> },
                  { key: "quantity", label: "Open qty", align: "right", render: (p) => num(p.quantity, 2) },
                  { key: "average_cost", label: "Avg cost", align: "right", render: (p) => num(p.average_cost, 2) },
                  { key: "cost_basis", label: "Cost basis", align: "right", render: (p) => money(p.cost_basis, 2) },
                  { key: "market_value", label: "Market value", align: "right", render: (p) => money(p.market_value, 2) },
                  { key: "unrealized_pnl", label: "Unrealised", align: "right", render: (p) => <span className={tone(p.unrealized_pnl)}>{money(p.unrealized_pnl, 2)}</span> },
                  { key: "realized_pnl", label: "Realised", align: "right", render: (p) => <span className={tone(p.realized_pnl)}>{money(p.realized_pnl, 2)}</span> },
                  { key: "income", label: "Income", align: "right", render: (p) => money(p.income, 2) },
                  { key: "lots", label: "Open lots", value: (p) => p.open_lots?.length ?? null, render: (p) => (p.open_lots ? <span className="xs">{p.open_lots.map((l: AnyObj) => `${num(l.quantity, 0)}@${num(l.cost_per_share, 2)} (${l.acquired})`).join("; ")}</span> : <span className="xs muted">pooled</span>) },
                ]} />
              </Card>
              <Card title="Transactions" flush>
                <DataTable<AnyObj> rows={t.rows} exportName="transactions" pageSize={20} columns={[
                  { key: "code", label: "ID", render: (r) => <Link to={`/lineage?transaction=${r.id}`} className="mono">{r.code}</Link> },
                  { key: "date", label: "Date" }, { key: "account", label: "Account" },
                  { key: "type", label: "Type", render: (r) => <span className={r.type === "buy" ? "pos" : r.type === "sell" ? "neg" : ""}>{r.type}</span> },
                  { key: "symbol", label: "Symbol", render: (r) => <span className="mono">{r.symbol ?? "–"}</span> },
                  { key: "quantity", label: "Qty", align: "right", render: (r) => num(r.quantity, 2) }, { key: "price", label: "Price", align: "right", render: (r) => num(r.price, 2) },
                  { key: "fees", label: "Fees", align: "right", render: (r) => money(r.fees, 2) }, { key: "amount", label: "Amount", align: "right", render: (r) => money(r.amount, 2) },
                  { key: "source", label: "Source", render: (r) => <span className="small text2">{r.source}</span> },
                ]} empty="No transactions match — import a transaction history on Connections." />
              </Card>
            </div>
          )}
        </QueryView>
      </div>
    </>
  );
}
