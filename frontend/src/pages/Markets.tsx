import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, PageHead, QueryView, Tabs } from "../components/ui";
import { Change, fmtBig, fmtPrice, SymbolSearch, TYPE_LABEL } from "../components/market";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

const LISTS = [
  { value: "uae", label: "UAE · DFM" }, { value: "us", label: "US" }, { value: "indices", label: "Indices" },
  { value: "bonds", label: "Bonds & rates" }, { value: "commodities", label: "Commodities" }, { value: "fx", label: "FX" }, { value: "crypto", label: "Crypto" },
] as const;
type ListKey = (typeof LISTS)[number]["value"];

export default function Markets() {
  const nav = useNavigate();
  const [list, setList] = useState<ListKey>(() => (localStorage.getItem("nx-mk-list") as ListKey) || "uae");
  const q = useQuery({ queryKey: ["mk-list", list], queryFn: () => api.get<AnyObj>(`/markets/lists/${list}`), refetchInterval: 60_000 });
  const choose = (v: ListKey) => { setList(v); try { localStorage.setItem("nx-mk-list", v); } catch { /* storage unavailable */ } };
  return (
    <>
      <PageHead title="Global Markets" desc="Live quotes, fundamentals, analyst consensus and news for stocks, ETFs, indices, bond funds, currencies and crypto on most exchanges — including the Dubai Financial Market." />
      <div className="mk-hero">
        <SymbolSearch autoFocus onPick={(s) => nav(`/markets/${encodeURIComponent(s.symbol)}`)} />
        <div className="row small" style={{ gap: 6, marginTop: 10 }}>
          <span className="muted">Try</span>
          {["EMAAR.AE", "EMIRATESNBD.AE", "DEWA.AE", "AAPL", "NVDA", "^TNX", "GC=F", "BTC-USD"].map((s) => (
            <Link key={s} className="chip" to={`/markets/${encodeURIComponent(s)}`}>{s}</Link>
          ))}
        </div>
        <div className="row" style={{ gap: 8, marginTop: 14 }}>
          <Link className="btn primary" to="/advisor">Ask the AI advisor</Link>
          <Link className="btn" to="/compare">Compare & build a report</Link>
          <Link className="btn" to="/valuation">Value a company</Link>
          <Link className="btn" to="/social">InstaFin feed</Link>
        </div>
      </div>
      <Tabs value={list} onChange={choose} tabs={LISTS.map((l) => ({ value: l.value, label: l.label }))} />
      <QueryView q={q} label="Loading quotes">
        {(d) => (
          <Card title={d.label} sub={d.note} flush>
            <div className="table-wrap">
              <table className="dt mk-table">
                <thead><tr><th>Symbol</th><th>Name</th><th className="r">Price</th><th className="r">Change</th><th className="r hide-sm">Market cap</th><th className="r hide-sm">Volume</th><th className="hide-sm">Type</th></tr></thead>
                <tbody>
                  {d.items.map((x: AnyObj) => (
                    <tr key={x.symbol} className="clickable" onClick={() => nav(`/markets/${encodeURIComponent(x.symbol)}`)}>
                      <td className="mono"><Link to={`/markets/${encodeURIComponent(x.symbol)}`} onClick={(e) => e.stopPropagation()}>{x.symbol}</Link></td>
                      <td className="ellipsis">{x.name}</td>
                      <td className="r num">{fmtPrice(x.price)} <span className="xs muted">{x.currency}</span></td>
                      <td className="r"><Change pct={x.change_pct} /></td>
                      <td className="r num hide-sm">{fmtBig(x.market_cap)}</td>
                      <td className="r num hide-sm">{fmtBig(x.volume)}</td>
                      <td className="hide-sm small text2">{TYPE_LABEL[x.type] ?? x.type}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card-foot xs muted">{d.source}. Quotes refresh every minute; exchanges may delay data.</div>
          </Card>
        )}
      </QueryView>
    </>
  );
}
