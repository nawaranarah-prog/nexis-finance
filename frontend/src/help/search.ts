/* Help Center search, page context and suggestions. Pure functions (no React) so the rules stay in one place.
   The articles themselves come from GET /api/help/articles (backend/app/content/help_articles.json); the ranking
   mirrors backend/app/services/help.py so the AI is grounded in the same articles a member would find. */

export type HelpLink = { label: string; path: string };
export type HelpArticle = { id: string; title: string; contexts: string[]; keywords: string[]; body: string; links: HelpLink[] };
export type HelpContext = "general" | "pulse" | "markets" | "compare" | "reports" | "my-nexis" | "advisor" | "billing" | "account";

const STOP = new Set(["a", "an", "the", "i", "my", "me", "do", "does", "how", "what", "is", "are", "to", "of", "in", "on", "for", "can", "and",
  "or", "it", "with", "why", "where", "when", "you", "your", "this", "that", "be", "get", "use", "about", "nexis"]);

export const normalize = (text: string) => text.toLowerCase().replace(/[^a-z0-9.\s]+/g, " ").replace(/\s+/g, " ").trim();
const tokens = (text: string) => normalize(text).split(" ").filter((t) => t.length > 1 && !STOP.has(t));

/** Rank articles: keyword phrase > title word > keyword word > keyword prefix > body word; page context breaks ties. */
export function searchArticles(articles: HelpArticle[], query: string, context?: HelpContext, limit = 6): HelpArticle[] {
  const q = ` ${normalize(query)} `;
  const toks = tokens(query);
  if (!toks.length && !q.trim()) return [];
  const scored: [number, HelpArticle][] = [];
  for (const a of articles) {
    const title = normalize(a.title).split(" ");
    const body = new Set(normalize(a.body).split(" "));
    const kws = a.keywords.map(normalize);
    let s = 0;
    for (const k of kws) if (k && q.includes(` ${k} `)) s += 6 + k.split(" ").length;
    for (const t of toks) {
      if (title.includes(t)) s += 4;
      if (kws.some((k) => k.split(" ").includes(t))) s += 3;
      else if (t.length >= 4 && kws.some((k) => k.startsWith(t))) s += 1.5;
      if (body.has(t)) s += 1;
    }
    if (s > 0 && context && a.contexts.includes(context)) s += 1;
    if (s > 0) scored.push([s, a]);
  }
  return scored.sort((x, y) => y[0] - x[0]).slice(0, limit).map(([, a]) => a);
}

/** Which kind of page the member is on — the page category only, never anything from the URL itself. */
export function contextFor(pathname: string, hash = ""): HelpContext {
  if (pathname.startsWith("/pulse")) return "pulse";
  if (pathname.startsWith("/markets")) return "markets";
  if (pathname.startsWith("/compare") || pathname.startsWith("/valuation")) return "compare";
  if (pathname.startsWith("/reports")) return "reports";
  if (pathname.startsWith("/my-nexis") || pathname.startsWith("/portfolio")) return "my-nexis";
  if (pathname.startsWith("/advisor") || pathname.startsWith("/assistant")) return "advisor";
  if (pathname.startsWith("/pro") || pathname.startsWith("/pricing") || (pathname.startsWith("/settings") && hash === "#plan")) return "billing";
  if (pathname.startsWith("/settings") || pathname.startsWith("/notifications")) return "account";
  return "general";
}

/** Suggested questions per page, each answered by a specific verified article (opened directly — no AI needed). */
const SUGGESTIONS: Record<HelpContext, [string, string][]> = {
  general: [["What can I do with Nexis Pulse?", "pulse-basics"], ["What's the difference between Free, Plus and Pro?", "plans"],
    ["How do I add an investment?", "add-investment"], ["How do I compare assets?", "compare-assets"], ["How does the sentiment score work?", "pulse-sentiment"]],
  pulse: [["What can I do with Nexis Pulse?", "pulse-basics"], ["How does the sentiment score work?", "pulse-sentiment"],
    ["How is sentiment different from analyst targets?", "sentiment-vs-targets"], ["Are Reddit discussions included?", "reddit"]],
  markets: [["How do I find and research an asset?", "find-asset"], ["Why is a price delayed or unavailable?", "data-timestamps"],
    ["How does the sentiment score work?", "pulse-sentiment"], ["How do I compare assets?", "compare-assets"]],
  compare: [["How do I compare assets?", "compare-assets"], ["How do I generate a report?", "generate-report"],
    ["How is sentiment different from analyst targets?", "sentiment-vs-targets"]],
  reports: [["How do I generate a report?", "generate-report"], ["How do I compare assets?", "compare-assets"]],
  "my-nexis": [["How do I add an investment?", "add-investment"], ["Why can't I add another investment?", "investment-limit"],
    ["What's the difference between Free, Plus and Pro?", "plans"], ["Why is a price delayed or unavailable?", "data-timestamps"]],
  advisor: [["How does the AI Advisor work?", "advisor"], ["Is Nexis financial advice?", "advice"], ["What's the difference between Free, Plus and Pro?", "plans"]],
  billing: [["What's the difference between Free, Plus and Pro?", "plans"], ["How do I upgrade or change my plan?", "upgrade-change-plan"],
    ["How do I cancel my subscription?", "cancel"], ["Why is my subscription not showing correctly?", "plan-not-showing"], ["My payment failed", "payment-failed"]],
  account: [["Where are my account settings?", "account-settings"], ["How do I cancel my subscription?", "cancel"], ["Why can't I add another investment?", "investment-limit"]],
};

export function suggestionsFor(context: HelpContext, articles: HelpArticle[]): { question: string; article: HelpArticle }[] {
  const byId = new Map(articles.map((a) => [a.id, a]));
  return SUGGESTIONS[context].flatMap(([question, id]) => (byId.has(id) ? [{ question, article: byId.get(id)! }] : []));
}

/** Only internal paths the app defines may be offered as navigation (articles are data; this is the gate). */
const ROUTES = [/^\/$/, /^\/markets$/, /^\/pulse$/, /^\/community-guidelines$/, /^\/compare$/, /^\/reports$/, /^\/my-nexis(\/investments|\/watchlist)?$/,
  /^\/pricing$/, /^\/advisor$/, /^\/ai-disclosure$/, /^\/settings(#plan)?$/, /^\/privacy$/, /^\/disclaimer$/, /^\/terms$/];
export const isKnownRoute = (path: string) => ROUTES.some((r) => r.test(path));
