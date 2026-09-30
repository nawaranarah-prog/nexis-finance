import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, PageHead, QueryView } from "../components/ui";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { num } from "../utils/format";

const LABEL: Record<string, string> = {
  holdings_mismatch: "Sources disagree on holdings",
  snapshot_vs_transactions: "Snapshot ≠ transaction history",
  possible_duplicate_transaction: "Possible duplicate transaction",
};

export default function Reconciliation() {
  const q = useQuery({ queryKey: ["intel", "reconciliation"], queryFn: () => api.get<AnyObj>("/intelligence/reconciliation") });
  return (
    <>
      <PageHead title="Cross-Source Reconciliation" desc="Where two sources describe the same account differently, Nexis flags a potential reconciliation issue and links to the original records. Nothing is overwritten automatically." />
      <QueryView q={q} label="Reconciling sources">
        {(r) => (
          <div className="stack">
            <div className={`banner ${r.count ? "warn" : "info"} small`}>{r.count ? `${r.count} potential reconciliation issue(s).` : "No inconsistencies found across sources."} {r.policy}</div>
            <Card title="Potential reconciliation issues" flush>
              <DataTable<AnyObj> rows={r.issues} exportName="reconciliation" columns={[
                { key: "type", label: "Issue", render: (i) => <span className="badge warn">{LABEL[i.type] ?? i.type}</span> },
                { key: "account", label: "Account" }, { key: "symbol", label: "Symbol", render: (i) => <b className="mono">{i.symbol}</b> },
                { key: "source_a", label: "Source A", render: (i) => <span className="small">{i.source_a} <span className="muted">({i.as_of_a})</span></span> },
                { key: "quantity_a", label: "Qty A", align: "right", render: (i) => num(i.quantity_a, 4) },
                { key: "source_b", label: "Source B", render: (i) => <span className="small">{i.source_b} <span className="muted">({i.as_of_b})</span></span> },
                { key: "quantity_b", label: "Qty B", align: "right", render: (i) => num(i.quantity_b, 4) },
                { key: "difference", label: "B − A", align: "right", render: (i) => <span className={i.difference ? "neg" : ""}>{num(i.difference, 4)}</span> },
                { key: "records", label: "Source records", sortable: false, render: (i) => (
                  <span className="small">{(i.source_record_ids ?? []).map((id: number) => <Link key={id} to={`/lineage?record=${id}`} style={{ marginRight: 6 }}>#{id}</Link>)}</span>) },
              ]} empty="Nothing to reconcile." />
            </Card>
          </div>
        )}
      </QueryView>
    </>
  );
}
