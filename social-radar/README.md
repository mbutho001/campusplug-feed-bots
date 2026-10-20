# Campus Plug Social Radar

Scans social platforms every 3 hours for **trending topics** and **image-based
jokes/memes** worth posting on the Campus Plug social page, then emails a
compact digest to the addresses in the DIGEST_RECIPIENTS secret.

**Video is excluded by design.** Every image is re-encoded to a small JPEG and
embedded inline, so a mail that arrives 8 times a day stays light on data.

## Running it

```bash
python3 agent.py --dry-run     # scan + write logs/preview.html, send nothing
python3 agent.py               # scan + send
python3 agent.py --no-images   # topics only, fast smoke test
./run.sh                       # what cron runs (adds locking + logging)
```

Schedule (installed in the user crontab):

```
5 */3 * * * /home/aiengine/claude-code/campus-plug/campusplug-social-agent/run.sh
```

00:05, 03:05, 06:05, 09:05, 12:05, 15:05, 18:05, 21:05 SAST. A run takes
5–8 minutes, almost all of it waiting out Reddit's rate limiter. `run.sh` holds
a `flock`, so a slow run is skipped rather than overlapped.

## Platform coverage — what is real

| Platform | Status | How |
|---|---|---|
| **Reddit** | Live, no key | Per-subreddit RSS. The `.json` endpoints are blocked for anonymous clients; RSS still carries titles, permalinks and direct `i.redd.it` image URLs. This is the main source of memes and image jokes. |
| **X (Twitter)** | Live, no key | Trending topic names for South Africa + worldwide, scraped from trends24. Topics only — X gives no free access to post images. |
| **Google Trends** | Live, no key | Official daily-trends RSS for ZA, with search volumes. Not asked for, but it is the best free read on what SA is searching *right now*. |
| **TikTok** | Needs a cookie | The Creative Center hashtag API rejects anonymous calls (`40101 no permission`). Paste a cookie into `config.json` to switch it on — see below. |
| **Instagram** | Needs a token | Meta serves no public trending feed. Hashtag search requires an Instagram **Business/Creator** account token. |
| **Facebook** | Needs a token | Same Graph API token; reads the Campus Plug Page feed. |

Any platform that cannot report says so in a "Platforms not reporting this run"
box at the bottom of the email, so a quiet platform is never mistaken for a
quiet internet. Nothing is ever invented to fill a gap.

### Enabling TikTok

1. Sign in at <https://ads.tiktok.com/business/creativecenter/> in Chrome.
2. DevTools → Network → refresh → click a `creative_radar_api` request →
   copy the full `Cookie` request header.
3. Paste it into `config.json` → `platforms.tiktok.creative_center_cookie`.

Cookies expire; when one dies the email reports `cookie rejected — refresh it`.

### Enabling Instagram + Facebook

Needs the Campus Plug IG account converted to Business/Creator and linked to a
Facebook Page, then a long-lived token from a Meta app with
`instagram_basic`, `instagram_manage_insights` and `pages_read_engagement`.
Fill in `platforms.meta.access_token`, `ig_business_id` and `fb_page_id`.
Instagram's hashtag API returns top posts for the hashtags in `meta_hashtags`;
`VIDEO` and `CAROUSEL` media are dropped, images only.

## How content is chosen

Ranking favours what fits Campus Plug: feed weight × position in feed, plus a
boost for anything matching `student_keywords` (NSFAS, res, exam, varsity, UCT,
load shedding…) or `sa_keywords` (Mzansi, Joburg, Eskom, amapiano…). Topics
appearing on more than one platform get boosted hardest, because
cross-platform overlap is the strongest trend signal available for free.

Gates every item passes:

- **No video** — `v.redd.it`, YouTube, Streamable, Gfycat, Redgifs, TikTok
  video URLs, `.mp4/.mov/.webm/.gifv` and Reddit galleries are dropped, and
  animated GIFs over 900 KB are rejected as data-heavy.
- **Really an image** — the `Content-Type` must be JPEG/PNG/WebP, the download
  is aborted past 3.5 MB, and the bytes must decode as an image.
- **Small** — resized to 600 px wide and re-encoded at falling quality until it
  fits 130 KB; the whole mail is capped at 2.5 MB.
- **Safe** — `banned_words` filter on titles.
- **Not a repeat** — every item and topic mailed is remembered in
  `state/seen.sqlite3` for 21 days, so the 8 daily mails do not echo.

Each run pulls a rotating slice of 7 of the 13 subreddits (`feeds_per_run`), so
the source mix keeps turning over.

## Tuning

Everything lives in `config.json`:

- `recipients` — add addresses to the list.
- `reddit_feeds` — add or drop subreddits (`sort` is `"hot"` or `"top?t=day"`).
  Verify a new one yields images before trusting it; text-heavy subs return
  nothing useful.
- `run.max_items_per_email` (14) and `max_topics_per_email` (12).
- `media.*` — image budget knobs.
- `student_keywords` / `sa_keywords` — what gets boosted.

Mail goes out through the existing GoDaddy mailbox by reusing
`~/claude-code/qconnec/qconnec-mail/mailer.py`'s credential loader — no second copy of the SMTP
password.

## Files

```
agent.py          orchestrator: collect, filter, rank, pack, send
config.json       all tuning
filters.py        video/safety gates + scoring
media.py          download, verify, downscale
render.py         HTML + plain-text email
send.py           SMTP via ~/claude-code/qconnec/qconnec-mail credentials
store.py          21-day dedupe (sqlite)
sources/          one adapter per platform
run.sh            cron entrypoint (flock + log rotation)
logs/             agent.log, cron.log, preview.html
```

## A note on reposting

The digest links every item to its original post. Reddit and Instagram content
belongs to whoever made it — credit or get permission before reposting on the
Campus Plug page. The blank Imgflip templates in each mail are the safe option:
caption those yourself.
