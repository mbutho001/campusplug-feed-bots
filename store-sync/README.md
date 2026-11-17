# store-sync: Plug Store ← Core Group (Apple)

Daily jobs in `tick.py`:

| Job | When (SAST) | Does |
|---|---|---|
| `store-sync` | 06:20 | looks for a new Core price list; on a new list or every Monday rebuilds the 40 listed products (Core best-sellers × student fit); Core stock + margin guards; ends/starts Plug Deals; front-card images; queues feed posts; emails changes/alerts |
| `store-check` | 13:20 | guards + deal start/end only |
| `store-dry` / `store-rebuild` | manual (`only`) | dry run / force a rebuild |

**Prices.** Regular = Core RRP incl. VAT × 1.20 (Core's promo RRP while Core runs a promo).
**Plug Deal** = RRP × 1.15, with the regular price shown as the "was" price. A product qualifies only after 7 days on sale at the regular price and 21 days since its last deal.

**Seasons** (`seasons.py`): Black November, festive gifting, year-end sales, back to campus (biggest), Easter, exams, Youth Month, winter break, Women's Month, Heritage/spring, early Black Friday, and a semester default. Each season sets the deal slots, round length, rest days between rounds and category focus. Payday weeks (25th–2nd) add a slot. When a new season starts, the job posts that season's kickoff to the feed.

**New price lists** (the link sits behind the reseller login). The job checks, in order: `state/pricelist_url.txt`, the price-list page, then probes Core's CDN for `YYYYMMDD_ARP_Price_list_*.xlsx`. If none is found but Core's live wholesale prices drift, it emails an alert, and the margin guard hides any variant with less than 5% margin. After Core's promo end date, promo SKUs stay hidden until a new list arrives.

**State** (private repo `campusplug-feed-state`, `store-sync/`): `state.json` (deals, history, hero, listed-since), `pricelist.xlsx`, `prices/<date>.json`, `core_cache.json`. Add `state/PAUSE` to stop the job.

**DB.** Uses app-db 031 `store_sync_state` / `store_sync_import` / `store_sync_apply`, gated by the `STORE_SYNC_TOKEN` secret, plus 030 `store_feed_queue`. These cannot touch orders, payments or settings.
