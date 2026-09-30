import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

/**
 * Small, safe markdown renderer for advisor answers: headings, paragraphs, bullet/numbered lists,
 * **bold**, *italic*, `code` and [links](https://…). Nothing is injected as HTML.
 */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(<Tag key={blocks.length} className="md-list">{list.items.map((it, i) => <li key={i}>{inline(it)}</li>)}</Tag>);
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const num = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || num) {
      const ordered = !!num;
      if (!list || list.ordered !== ordered) { flush(); list = { ordered, items: [] }; }
      list.items.push((bullet ?? num)![1]);
      continue;
    }
    flush();
    if (!line.trim()) continue;
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push(<div key={blocks.length} className={`md-h md-h${h[1].length}`}>{inline(h[2])}</div>);
    } else if (/^-{3,}$/.test(line.trim())) {
      blocks.push(<hr key={blocks.length} className="md-hr" />);
    } else {
      blocks.push(<p key={blocks.length} className="md-p">{inline(line)}</p>);
    }
  }
  flush();
  return <div className="md">{blocks}</div>;
}

const TOKEN = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g;

function inline(s: string): ReactNode {
  const parts = s.split(TOKEN);
  return parts.map((p, i) => {
    if (p.startsWith("**") && p.endsWith("**")) return <b key={i}>{p.slice(2, -2)}</b>;
    if (p.startsWith("`") && p.endsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(p);
    if (link) {
      const href = link[2];
      if (href.startsWith("/")) return <Link key={i} to={href}>{link[1]}</Link>;
      if (/^https?:\/\//.test(href)) return <a key={i} href={href} target="_blank" rel="noreferrer noopener">{link[1]}</a>;
      return <Fragment key={i}>{link[1]}</Fragment>;
    }
    if (p.startsWith("*") && p.endsWith("*") && p.length > 2) return <i key={i}>{p.slice(1, -1)}</i>;
    return <Fragment key={i}>{p}</Fragment>;
  });
}
