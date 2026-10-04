# Nexis Pulse, My Nexis and the legal foundation

How the anonymous discussion layer, the personal layer and the legal records fit together, and what an operator needs
to do. Code references are relative to `backend/` and `frontend/src/`.

## The three layers

| Layer | Public? | Where |
| --- | --- | --- |
| Financial intelligence (markets, research, advisor) | yes | existing pages |
| **My Nexis** — investments, watchlist, "Your Nexis Today", per-asset intelligence, alerts | private to the account | `pages/MyNexis.tsx`, `/api/me/*`, `services/portfolio.py` |
| **Nexis Pulse** — anonymous community discussion plus Nexis editorial context | yes | `pages/pulse/*`, `/api/pulse/*`, `services/pulse.py` |

## Anonymity: how it is enforced

* Pulse has its own tables (`pulse_discussions`, `pulse_comments`, …; `app/models/pulse.py`), separate from the
  Finstagram feed, which is a public-identity product. Anonymity therefore does not depend on every Finstagram
  endpoint filtering correctly.
* `author_id` is stored for moderation, rate limits, deletion and lawful requests. Public serializers in
  `services/pulse.py` build the author from constants (`{"display_name": "Anonymous"}` or Nexis) and never put
  `author_id` in output. The only per-viewer facts are `is_mine` and `is_op` (the thread's original poster).
* Public ids are random (`public_id`), not row ids.
* Moderators see content, flags and report reasons, never who wrote or reported anything. Account-level actions
  (pausing posting) are applied through the content.
* `tests/test_pulse_anonymous.py` scans every public response for identity keys and the test accounts' email,
  username and display name.

## Never fabricated

No personas, no generated comments, no seeded reactions or follows. Counts are rows real members created; "Trending"
uses real replies (weighted by distinct people), reactions, follows and saves, and says so when nothing qualifies.
Editorial content is labelled Nexis and, when drafted with AI, "AI-assisted" (`/ai-disclosure`).

## Editorial engine

`services/pulse_editorial.py`. `publish()` is the only writer: it stores sources (title, URL, publisher, published
and retrieved times), diffs the new content against the old, and appends dated `pulse_discussion_updates`
(development, what changed, new bull/bear argument, open question). Followers get one notification per material
update. Inputs:

* `sync_asset(symbol)` — from the evidence-based market debate in `services/market_pulse.py`.
* `sync_theme(key)` — rates, oil, AI infrastructure, UAE markets, US equities, from recent sourced events.

Nothing is published without enough sourced coverage. It runs from the background tick (`services/pulse_engine.py`,
nudged by page views and the daily cron). Nexis Research essays are published with `python -m app.seeds.research`
(never back-dated).

## Safety and moderation

`services/pulse_safety.py` flags guaranteed returns, inside-information claims, coordinated trading, impersonation,
off-platform solicitation and suspicious promotion. Most flags keep the post visible and queue it; solicitation and
coordination hold it until reviewed. Members report through `/api/pulse/reports`; moderators work in `/moderation`.

Give someone the moderator role on the server (never from the web):

```bash
cd backend && python -m app.cli.set_role person@example.com moderator
```

## Legal

* Pages: `/terms`, `/privacy`, `/disclaimer`, `/community-guidelines`, `/ai-disclosure` (`pages/Legal.tsx`).
* Versions: `app/core/legal.py` (keep in step with `VERSION` in `Legal.tsx`). Bumping a version asks every member to
  accept again before posting.
* Acceptance is recorded in `legal_acceptances` (user, terms version, privacy version, time, method). Email sign-up
  requires it in the request; Google/phone sign-ups record it on return or through the prompt.
* Operator: Nawar Anarah (Dubai, UAE); governing law: Dubai / UAE; contact: nawaranarah@gmail.com. Having qualified
  counsel review the documents is still recommended.

## Notifications

Categories: investment, watchlist, market, earnings, major news, Pulse alerts (new discussions on tracked assets) and
discussion updates (replies to you, updates to followed discussions). Each is immediate, daily, weekly or off.
Digest categories don't light the bell; `alerts.summarise_digests` posts one in-app digest per period. Email is
prepared but not sent until an email provider is connected.

## Migration 0012

Creates the Pulse, moderation and legal tables, adds `users.role`, `users.posting_suspended_until` and
`user_notifications.delivery`, then copies existing member and Nexis Research discussions (with human comments and
saves) out of `posts`, skipping all persona content, and hides the originals from the Finstagram feed. Old
`/pulse/discussion/<post id>` links redirect. `alembic downgrade 0011` unhides the original posts.
