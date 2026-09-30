import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, PageHead, QueryView, Tabs } from "../components/ui";
import { Change, fmtBig, fmtPrice, SymbolSearch, TYPE_LABEL } from "../components/market";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

const LISTS = [
  { value: "uae", label: "UAE shares" }, { value: "uae_bonds", label: "UAE bonds & sukuk" }, { value: "indices", label: "Indices" },
  { value: "us", label: "US" }, { value: "bonds", label: "US rates" }, { value: "commodities", label: "Commodities" },
  { value: "fx", label: "FX" }, { value: "crypto", label: "Crypto" },
] as const;
type ListKey = (typeof LISTS)[number]["value"];
type Sort = "market_cap" | "change_pct" | "name";

export default function Markets() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const initial = (sp.get("list") as ListKey) || (localStorage.getItem("nx-mk-list") as ListKey) || "uae";
  const [list, setList] = useState<ListKey>(LISTS.some((l) => l.value === initial) ? initial : "uae");
  const [sector, setSector] = useState<string>("All");
  const [exchange, setExchange] = useState<"All" | "ADX" | "DFM">("All");
  const [sort, setSort] = useState<Sort>("market_cap");
  const q = useQuery({ queryKey: ["mk-list", list], queryFn: () => api.get<AnyObj>(`/markets/lists/${list}`), refetchInterval: 60_000 });
  const choose = (v: ListKey) => {
    setList(v); setSector("All"); setExchange("All"); setSp({ list: v }, { replace: true });
    try { localStorage.setItem("nx-mk-list", v); } catch { /* storage unavailable */ }
  };
  const rows = useMemo(() => {
    let items: AnyObj[] = q.data?.items ?? [];
    if (list === "uae") {
      if (sector !== "All") items = items.filter((x) => x.sector === sector);
      if (exchange !== "All") items = items.filter((x) => x.exchange === exchange);
      items = [...items].sort((a, b) => sort === "name" ? a.name.localeCompare(b.name) : (b[sort] ?? -1e18) - (a[sort] ?? -1e18));
    }
    return items;
  }, [q.data, list, sector, exchange, sort]);
  const compareSector = () => {
    const top = rows.filter((x) => x.type === "equity").slice(0, 6).map((x) => x.symbol);
    nav(`/compare?s=${encodeURIComponent(top.join(","))}&p=1y`);
  };
  return (
    <>
      <PageHead title="UAE & Global Markets" desc="Every share on Abu Dhabi (ADX) and Dubai (DFM), UAE government bonds and sukuk, plus global stocks, indices, commodities, currencies and crypto — live quotes, fundamentals, analyst ratings and news." />
      <div className="mk-hero">
        <SymbolSearch autoFocus onPick={(s) => nav(`/markets/${encodeURIComponent(s.symbol)}`)} placeholder="Search any UAE or global stock, bond, sukuk, index or currency…" />
        <div className="row small" style={{ gap: 6, marginTop: 10, flexWrap: "wrap" }}>
          <span className="muted">Popular</span>
          {["FAB.AD", "EMAAR.AE", "ALDAR.AD", "EMIRATESNBD.AE", "ADNOCGAS.AD", "EAND.AD", "DEWA.AE", "IHC.AD", "UAE0734USD.BOND"].map((s) => (
            <Link key={s} className="chip" to={`/markets/${encodeURIComponent(s)}`}>{s}</Link>
          ))}
        </div>
      </div>
      <Tabs value={list} onChange={choose} tabs={LISTS.map((l) => ({ value: l.value, label: l.label }))} />
      {list === "uae" && q.data?.sectors && (
        <div className="mk-filters">
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {["All", ...q.data.sectors].map((x: string) => <button key={x} className={`chip ${sector === x ? "on" : ""}`} onClick={() => setSector(x)}>{x}</button>)}
          </div>
          <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
            <div className="seg">{(["All", "ADX", "DFM"] as const).map((x) => <button key={x} className={exchange === x ? "on" : ""} onClick={() => setExchange(x)}>{x === "All" ? "Both exchanges" : x}</button>)}</div>
            <div className="seg">{([["market_cap", "Largest"], ["change_pct", "Top movers"], ["name", "A–Z"]] as const).map(([k, l]) => <button key={k} className={sort === k ? "on" : ""} onClick={() => setSort(k)}>{l}</button>)}</div>
            {sector !== "All" && <button className="btn sm primary" onClick={compareSector}>Compare the top {sector.toLowerCase()} names →</button>}
          </div>
        </div>
      )}
      <QueryView q={q} label="Loading quotes">
        {(d) => (
          <Card title={d.label} sub={list === "uae" ? `${rows.length} shown · ${d.note}` : d.note} flush>
            <div className="table-wrap">
              <table className="dt mk-table">
                <thead><tr><th>Symbol</th><th>Name</th>{list === "uae" && <th className="hide-sm">Sector</th>}<th className="r">Price</th><th className="r">Change</th>
                  {list === "uae_bonds" ? <><th className="r hide-sm">Yield</th><th className="r hide-sm">Coupon</th><th className="r hide-sm">Maturity</th></>
                    : <><th className="r hide-sm">Market cap</th><th className="r hide-sm">Volume</th></>}</tr></thead>
                <tbody>
                  {rows.map((x: AnyObj) => (
                    <tr key={x.symbol} className="clickable" onClick={() => nav(`/markets/${encodeURIComponent(x.symbol)}`)}>
                      <td className="mono"><Link to={`/markets/${encodeURIComponent(x.symbol)}`} onClick={(e) => e.stopPropagation()}>{x.symbol.replace(/\.BOND$/, "")}</Link>
                        {x.exchange && list === "uae" && <span className="xs muted"> · {x.exchange}</span>}</td>
                      <td className="ellipsis">{x.name}</td>
                      {list === "uae" && <td className="hide-sm small text2">{x.sector}</td>}
                      <td className="r num">{fmtPrice(x.price)} <span className="xs muted">{x.currency}</span></td>
                      <td className="r"><Change pct={x.change_pct} /></td>
                      {list === "uae_bonds" ? (
                        <>
                          <td className="r num hide-sm">{x.yield_to_maturity !== undefined && x.yield_to_maturity !== null ? `${(x.yield_to_maturity * 100).toFixed(2)}%` : TYPE_LABEL[x.type] ?? x.type}</td>
                          <td className="r num hide-sm">{x.coupon !== undefined ? `${(x.coupon * 100).toFixed(3)}%` : "—"}</td>
                          <td className="r num hide-sm">{x.maturity ?? "—"}</td>
                        </>
                      ) : (
                        <>
                          <td className="r num hide-sm">{fmtBig(x.market_cap)}</td>
                          <td className="r num hide-sm">{fmtBig(x.volume)}</td>
                        </>
                      )}
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
