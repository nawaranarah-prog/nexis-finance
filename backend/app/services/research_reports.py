"""PDF reports from saved research runs (Time Machine scenarios, Devil's Advocate challenges).

A report is a faithful print of one stored run — the same numbers, flags, citations and sources the member saw — so it
never re-computes or re-asks the AI. Reports belong to the member (``owner_id``); the download route already returns
404 to anyone else.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.models import Report, ResearchItem, ResearchRun
from app.reporting.pdf import Spacer, mm, para, table
from app.services.market_reports import _store

# Standard PDF fonts cover Windows-1252 only; map the few other characters our text uses.
_MAP = str.maketrans({"−": "-", "β": "beta ", "≈": "~", "→": "->", "⚠": "!", "≥": ">=", "≤": "<=", " ": " "})
FOOTER = "Nexis Finance research tools. Educational information, not investment advice."


def _t(s: Any) -> str:
    text = str(s if s is not None else "").translate(_MAP)
    text = text.encode("cp1252", errors="replace").decode("cp1252")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(v: Any, d: int = 1) -> str:
    if v is None:
        return "-"
    return f"{'+' if v > 0 else ''}{v:.{d}f}%"


def _scenario_story(item: ResearchItem, run: ResearchRun) -> list[Any]:
    o, inp = run.output, run.inputs
    p = o["portfolio"]
    shocks = (
        ", ".join(f"{k} {v:+g}{'bp' if k == 'rates_us10y' else '%'}" for k, v in (inp.get("factor_shocks") or {}).items())
        or "none"
    )
    own = ", ".join(f"{k} {v:+g}%" for k, v in (inp.get("asset_shocks") or {}).items()) or "none"
    story: list[Any] = [
        para(_t(item.title), "h1"),
        para(_t(f"Time Machine scenario - run of {run.created_at:%Y-%m-%d %H:%M} UTC - method {run.model_version}"), "subtitle"),
        para(_t(f"Market moves: {shocks}. Your own moves: {own}. History window: {inp.get('window_weeks')} weeks."), "body"),
    ]
    if o["lines"]:
        story += [
            Spacer(1, 3 * mm),
            para(
                _t(
                    f"Estimated change {p['change']:,.0f} ({_pct(p['change_pct'])}); wide range {p['wide_low']:,.0f} to {p['wide_high']:,.0f}."
                ),
                "h2",
            ),
        ]
        rows = [["Holding", "Value", "Method", "Impact", "95% range", "Notes"]]
        for ln in o["lines"]:
            st = ln.get("stats") or {}
            how = ""
            if ln["method"] == "factor":
                how = (
                    "; ".join(f"{k}: beta {b:.2f}" for k, b in (st.get("beta") or {}).items())
                    + f"; R2 {st.get('r2', 0):.2f}; {st.get('weeks')} weeks"
                )
            elif ln["method"] == "duration":
                how = f"duration {st.get('modified_duration', 0):.2f}, convexity {st.get('convexity', 0):.1f}"
            notes = "; ".join([how, *ln.get("flags", [])]).strip("; ")
            rng = f"{_pct(ln.get('low'))} to {_pct(ln.get('high'))}" if ln["method"] == "factor" else "-"
            rows.append([_t(ln["symbol"]), f"{ln['value']:,.0f}", _t(ln["method"]), _pct(ln.get("pct")), rng, _t(notes)])
        story.append(table(rows, col_widths=[25 * mm, 20 * mm, 20 * mm, 17 * mm, 28 * mm, 60 * mm]))
    if o.get("valuation"):
        v = o["valuation"]
        story += [Spacer(1, 3 * mm), para(_t(f"Valuation check: {v['name']}"), "h2"),
                  para(_t(f"DCF value per share {v['before']:.2f} -> {v['after']:.2f} {v['currency']} ({_pct(v.get('change_pct'))})."), "body")]  # fmt: skip
        story += [para(_t(f"! {f}"), "small") for f in v.get("flags", [])]
    story += [Spacer(1, 3 * mm), para("How to read this", "h2"), *[para(_t(n), "small") for n in o.get("notes", [])]]
    return story


def _thesis_story(item: ResearchItem, run: ResearchRun) -> list[Any]:
    o, inp = run.output, run.inputs
    evidence = {e["id"]: e for e in (run.snapshot or {}).get("evidence", [])}

    def cites(cs: list[dict[str, Any]]) -> list[Any]:
        return [para(_t(f'{c["label"]}: "{c["quote"]}"'), "small") for c in cs]

    story: list[Any] = [
        para(_t(item.title), "h1"),
        para(
            _t(
                f"Devil's Advocate - {inp.get('symbol')} - challenge of {run.created_at:%Y-%m-%d %H:%M} UTC - method {run.model_version}"
            ),
            "subtitle",
        ),
        para(_t(f"Thesis: {inp.get('thesis')}"), "body"),
        Spacer(1, 3 * mm),
        para("Assumption check", "h2"),
        table(
            [["Claim", "Type", "Status"]]
            + [[_t(f"{c['id']}: {c['text']}"), c["kind"], c["status"]] for c in o.get("claims", [])],
            col_widths=[120 * mm, 25 * mm, 25 * mm],
        ),
    ]
    for title, key in (("The strongest case against", "counter_case"), ("Risks", "risks")):
        story += [Spacer(1, 2 * mm), para(title, "h2")]
        for pt in o.get(key, []) or []:
            flag = (
                f" (figures not found in the evidence: {', '.join(pt['unverified_numbers'])})"
                if pt.get("unverified_numbers")
                else ""
            )
            story += [para(_t(pt["text"] + flag), "body"), *cites(pt["citations"])]
    story += [Spacer(1, 2 * mm), para("What would change the view", "h2")]
    story += [
        para(_t(f"{'Would weaken it' if s['direction'] == 'weakens' else 'Would strengthen it'}: {s['text']}"), "body")
        for s in o.get("signals", [])
    ]
    story += [Spacer(1, 2 * mm), para("Gaps and limits", "h2"), *[para(_t(g), "body") for g in o.get("gaps", [])]]
    val = o.get("validation") or {}
    story.append(para(_t(f"Checks: {val.get('citations', 0)} citations from the AI, {val.get('dropped', 0)} removed because the quote "
                         f"wasn't in that evidence item; {val.get('points_dropped', 0)} argument(s) removed for lack of a valid citation."), "small"))  # fmt: skip
    story += [Spacer(1, 2 * mm), para("Evidence", "h2")]
    for e in evidence.values():
        story.append(
            para(
                _t(f"{e['label']} - {e.get('publisher') or e['source_kind']}: {e['title']}. {e['excerpt']} {e.get('url') or ''}"),
                "small",
            )
        )
    return story


def generate(db: Session, item: ResearchItem, run: ResearchRun, owner_id: int) -> Report:
    if run.status != "completed":
        raise ValidationFailed("Only a completed run can be turned into a report.")
    if run.run_type == "scenario":
        story, kind = _scenario_story(item, run), "scenario"
    elif run.run_type == "thesis":
        story, kind = _thesis_story(item, run), "thesis"
    else:
        raise ValidationFailed("Reports are available for scenarios and thesis challenges.")
    return _store(db, f"{item.title} ({run.created_at:%Y-%m-%d})", kind, story, FOOTER,
                  {"item_id": item.id, "run_id": run.id, "model_version": run.model_version}, [kind], owner_id=owner_id)  # fmt: skip
