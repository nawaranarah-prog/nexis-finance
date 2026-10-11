# SEO and server rendering: proposal (overhaul Step 9)

The brief says to propose the approach before building it. Nothing in this document is implemented yet.

## Where we are

* The site is a React single-page app. Search engines and link previews (WhatsApp, X, LinkedIn) receive the same
  `index.html` for every address, with the site's generic title and description and an empty `<div id="root">`.
  Google renders JavaScript, though late and only partly. Most link-preview bots don't render it at all.
* **Already server-rendered:** Pulse discussions (`/pulse/d/:id/:slug`). The frontend rewrites them to
  `api/seo/render/...`, which returns the deployed `index.html` with that discussion's title, description, canonical
  URL, Open Graph tags and JSON-LD injected. The app then starts normally. There is also a sitemap.
* **Not covered:** instrument pages (`/markets/EMAAR.AE`, the most valuable for search), Finstagram posts, the home
  page, pricing, the research tool pages and the legal pages.

## Options

| | What it is | Benefit | Cost and risk |
|---|---|---|---|
| **A. Extend the head injection (recommended)** | Generalise what Pulse does: a server route per public page type fills `<title>`, description, canonical, Open Graph and JSON-LD, plus a short **visible text summary** inside `#root`. React replaces it on load, so users see no difference. The frontend's `vercel.json` rewrites those paths. | Correct previews everywhere and indexable text for instruments, posts, home and pricing. Reuses tested code (`seo.py`). No change to the React app. Small, reversible. | One extra API hop for those page loads (cached at the edge for a few minutes). The summary is plain text, not the full page. |
| B. Prerender static pages at build | Render home, pricing, legal and about to HTML at build time | Fast; fully static | Doesn't cover instruments or posts (thousands, changing data). Adds a build step. |
| C. Full SSR (React Router framework mode) | Render every page on the server | Best fidelity | A large rewrite of routing, data loading and auth. Every page would need to run on the server. A big regression risk for a modest gain over A. |

## Recommendation: A, in this order

1. **Instrument pages** `/markets/:symbol`: name, exchange, latest price and change with its as-of time, sector,
   a one-paragraph profile, and the latest headlines (titles and links). JSON-LD: `FinancialProduct` / `Corporation`.
2. **Finstagram posts** `/finstagram/p/:id`: the headline, source and time; the image as `og:image` when the
   publisher allows embedding.
3. **Static pages** (home, pricing, legal): title, description and a short visible summary. Pricing reads
   `plans.PRICES`, so the text never disagrees with checkout.
4. **Members-only tools** (World Graph, Time Machine, Devil's Advocate, My research): a public description only,
   `noindex` for saved items, and **never** member data.
5. **Sitemap:** add instrument pages for the curated lists and the recent news posts.

**Checks to add with it:**
* a test per page type: HTML from the server route contains the right title, canonical and JSON-LD, and no member
  data;
* Lighthouse SEO on production;
* link-preview checks (Open Graph debugger output saved in `tests/`).

**Estimate:** about one working session. It can be reverted by removing the rewrites in `frontend/vercel.json`.

**Decision needed:** approve A, or choose B or C.
