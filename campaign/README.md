# Campus Plug traction campaign (13 Nov 2026 – 31 Jan 2027)

Runs on the feed-bots tick (GitHub Actions). Nothing here needs the home PC.

| SAST | Job | What |
|---|---|---|
| 06:45 | `campaign-make` | Today's plan: promo posters (portrait/square/story), UGC-style Short, daily poll |
| 08:15 Wed/Sat | post | WhatsApp Channel promo |
| 10:00 / 10:05 | post | Daily poll in the app feed (bot account) + native Threads poll; X poll Tue/Thu |
| 12:00 Mon/Thu/Sat | post | FB + IG story |
| 12:00 Mon | post | Last week's Plug Pulse results poster (only if a poll got 5+ votes) |
| 13:30 Thu | post | Brand-battle poll poster on FB + IG |
| 14:15 → 14:30 | `campaign-pulse` | Best audited trend-desk post of the day → Threads + FB (+ X) |
| 16:00 Tue/Fri | post | Short → TikTok (inbox draft until audit), YouTube (once secrets exist), FB video |
| 18:00 Mon/Wed/Fri/Sat | post | Promo carousel → FB + IG + Threads; X Mon/Fri 18:10; LinkedIn Wed |
| 21:45 | `campaign-collect` | Poll results from app, Threads, X → `state/poll-results.json` |
| Mon 08:30 | `campaign-report` | Plug Pulse email (HTML + CSV) to STATS_RECIPIENTS |

Phases follow the Plug Seasons milestones in the brand proposals: Get plugged in → Pens Down (26 Nov) →
Festive (8 Dec) → Results & Ready (1 Jan) → Registration (19 Jan). Brand battles in `polls.json` are tagged
with the proposal brands they serve (`for`), so the Monday report doubles as pitch evidence.

## Prizes: teaser until funded

`prizes.json` is the only source of prize claims. While every prize is `pending`, copy says prize drops
are *coming*, and `captions.guard()` rejects "win", rand amounts, "redeem", "giveaway" and old points
claims, so even the LLM rewrite can't promise a prize. To make a prize live, set on that prize:
`status: "live"`, `funded_by`, `value_rand`, `entry_opens`, `entry_closes`, `draw_date`, `rules_url`
(a published rules page), then push. All six must be set or it stays teaser.

Before going live (SA Consumer Protection Act s36 + Meta promotion rules):
- free entry; rules published before entry opens (prize, value, sponsor, dates, how winners are chosen and told);
- draws 18+ only (Plug Seasons draw is age-gated); Campus Plug witnesses the draw (024/025);
- no "share to enter" or tagging on personal timelines; posts carry "not sponsored or run by Meta";
- if a brand funds it, its name and the prize terms must match the signed agreement.

## Controls

- Pause everything: create `campaign/state/PAUSE` in the state repo.
- Dry run in the cloud: Actions → feed → Run workflow, `only` = `campaign-dry`.
- Per-option app poll splits need migration `campusplug-app-db/028_poll_results.sql` (not applied yet);
  until then the report uses app vote totals plus the Threads/X splits.
- UGC-style Shorts use real app screens and a Campus Plug voice. They never show an invented student or a
  made-up testimonial. Real student UGC comes later via Plug Studio creators with signed releases.
