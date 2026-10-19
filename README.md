# Campus Plug feed bots (cloud)

Runs the bots that post to the Campus Plug app feed on GitHub Actions, so posting no longer
depends on one PC being switched on:

- **trend-bots/**: SA news and trend desk (Ayanda, Lwazi, Naledi) plus the community personas.
  Free Groq writes, free Gemini audits, posted through the token-gated `post_engagement` RPC.
- **opportunities/**: collects open SA internships, learnerships and bursaries, and the
  opportunity bots (Nomvula, Tshepo) post two listings an hour through `post_opportunity`.

`.github/workflows/feed.yml` fires every 10 minutes from 06:00 to 23:50 SAST, and `tick.py`
runs whatever is due (`python3 tick.py --list` shows the plan). Keys come from GitHub Secrets.
State (`engine.db`, seen listings, logs) lives in a separate private repo and is pushed back
after every tick.

To pause the trend desk, add `trend-bots/state/PAUSE` to the state repo. To stop everything,
disable the workflow in the Actions tab. To run one job now, go to Actions → feed →
Run workflow and set `only` (for example `publish`).
