import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, PageHead, QueryView } from "../components/ui";
import { useStrategies } from "../hooks/queries";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { num, spct, tone } from "../utils/format";

export default function QuantStrategies() {
  const strategies = useStrategies();
  const bts = useQuery({ queryKey: ["backtests"], queryFn: () => api.get<AnyObj[]>("/backtests") });
  return (
    <>
      <PageHead title="Quant Strategies" desc="Implemented strategy classes. Each receives only the market history up to its signal date and returns target weights; the engine executes them on the next bar." />
      <Card title="Signal and execution contract">
        <ol className="small text2" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li>On each signal date <i>t</i> (per the strategy's schedule), the engine passes a history window ending at <i>t</i>'s close — later bars do not exist from the strategy's point of view.</li>
          <li>The strategy returns target weights (or keeps holdings). Weights are validated: finite, no leverage (gross ≤ 100%), no shorts for long-only strategies, no new positions in assets without a price at <i>t</i>.</li>
          <li>Orders execute at the <b>next open</b> (default) or <b>next close</b> — never at the price used to form the signal — with commission and slippage on traded notional.</li>
          <li>Missing bars: untradable positions are held and valued at the last close; delisted positions convert to cash at their final close.</li>
        </ol>
      </Card>
      <div style={{ marginTop: 12 }}>
        <QueryView q={strategies}>
          {(list) => (
            <div className="grid g2">
              {list.map((s) => {
                const runs = (bts.data ?? []).filter((b) => b.strategy_key === s.key).slice(0, 4);
                return (
                  <Card key={s.key} title={s.name} sub={<code>{s.key}</code>} actions={<Link className="btn sm primary" to={`/backtesting?strategy=${s.key}`}>Configure backtest</Link>}>
                    <p className="text2" style={{ marginTop: 0 }}>{s.description}</p>
                    <table className="dt">
                      <thead><tr><th>Parameter</th><th>Default</th><th>Range / choices</th><th>Description</th></tr></thead>
                      <tbody>
                        {s.params.map((p) => (
                          <tr key={p.name}>
                            <td className="mono">{p.name}</td><td className="mono">{String(p.default)}</td>
                            <td className="small">{p.choices ? p.choices.join(" | ") : p.minimum != null ? `${p.minimum} – ${p.maximum}` : p.type}</td>
                            <td className="wrap small text2">{p.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {runs.length > 0 && (
                      <>
                        <div className="divider" />
                        <div className="small text2" style={{ marginBottom: 4 }}>Recent backtests</div>
                        <table className="dt"><tbody>
                          {runs.map((b) => (
                            <tr key={b.id}>
                              <td><Link to={`/backtesting?id=${b.id}`}>{b.experiment_code}</Link></td><td className="small">{b.name}</td>
                              <td className={`r ${tone(b.headline.cumulative_return)}`}>{spct(b.headline.cumulative_return)}</td>
                              <td className="r">Sharpe {num(b.headline.sharpe_ratio, 2)}</td>
                            </tr>
                          ))}
                        </tbody></table>
                      </>
                    )}
                  </Card>
                );
              })}
            </div>
          )}
        </QueryView>
      </div>
    </>
  );
}
