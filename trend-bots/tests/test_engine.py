"""Offline tests: parsing, clustering, scoring, risk rules, editor validation, scheduling, idempotency.
Run: python3 -m unittest discover -s tests -v"""
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import analyst, db, editor, publisher, sources  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text())
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


def item(title, src="news24", name="News24", tier=1, hours_ago=2, summary="", kind="news", url=None):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"id": title, "source_id": src, "source_name": name, "tier": tier, "kind": kind, "title": title,
            "summary": summary, "url": url or f"https://www.{src}.com/{abs(hash(title))}", "published_at": ts,
            "discovered_at": ts}


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>NSFAS opens 2027 applications &amp; extends deadline</title><link>https://www.sabcnews.com/a?utm_source=x</link>
<description>&lt;p&gt;Students can apply online.&lt;/p&gt;</description><pubDate>Sat, 17 Oct 2026 08:00:00 +0200</pubDate></item>
<item><title>No link item</title></item></channel></rss>"""


class FakeHttp:
    def __init__(self, body):
        self.body = body

    def get(self, url, **kw):
        return mock.Mock(content=self.body, text=self.body.decode()), None


class TestSources(unittest.TestCase):
    def test_rss_parse(self):
        items, err = sources.news_rss(FakeHttp(RSS), {"id": "sabc", "name": "SABC", "url": "x", "tier": 1})
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "NSFAS opens 2027 applications & extends deadline")
        self.assertEqual(items[0]["summary"], "Students can apply online.")
        self.assertEqual(items[0]["published_at"].hour, 6)  # +0200 -> UTC

    def test_news24_naive_date_is_sast(self):
        self.assertEqual(sources.parse_date("Saturday Oct 17 2026 19:21:59").hour, 17)

    def test_canonical_url_strips_tracking(self):
        self.assertEqual(sources.url_id("https://x.com/a/?utm_source=f&id=2"), sources.url_id("https://X.com/a?id=2"))

    def test_google_news_outlet_split(self):
        body = b"<rss><channel><item><title>Chiefs lose in Zambia - KickOff</title><link>https://news.google.com/x</link></item></channel></rss>"
        items, _ = sources.news_rss(FakeHttp(body), {"id": "g", "name": "Google News ZA", "url": "x", "tier": 2, "aggregator": True})
        self.assertEqual((items[0]["title"], items[0]["source_name"]), ("Chiefs lose in Zambia", "KickOff"))


class TestAnalyst(unittest.TestCase):
    def test_keywords_match_whole_words(self):
        self.assertEqual(analyst._hits(" a big chance ", ["anc"]), [])
        self.assertEqual(analyst._hits(" the anc said ", ["anc"]), ["anc"])
        self.assertEqual(analyst._hits(" graduation day ", ["graduat*"]), ["graduat"])

    def test_cluster_merges_same_story_and_counts_outlets(self):
        items = [item("Chiefs fall to Red Arrows in Zambia CAF clash", "kickoff", "KickOff", 2),
                 item("Kaizer Chiefs fall to Red Arrows in Zambia", "citizen", "The Citizen", 2),
                 item("Petrol price to drop in November", "iol", "IOL")]
        cl = analyst.score_clusters(CFG, items, [], now=NOW)
        chiefs = [c for c in cl if "Chiefs" in c["headline"]][0]
        self.assertEqual(len(cl), 2)
        self.assertEqual(chiefs["verification"], "corroborated")
        self.assertIn("sport_football", chiefs["categories"])

    def test_high_risk_single_source_rejected(self):
        cl = analyst.score_clusters(CFG, [item("Student accused of murder in Durban res")], [], now=NOW)
        self.assertEqual(cl[0]["risk"], "high")
        self.assertIn("high-risk claim from a single source", cl[0]["rejects"])

    def test_stale_and_social_only_rejected(self):
        cl = analyst.score_clusters(CFG, [item("Old story about load shedding schedule", hours_ago=50)], [], now=NOW)
        self.assertIn("stale", cl[0]["rejects"])  # load_recent also drops it before scoring
        cl = analyst.score_clusters(CFG, [item("Where to find cheap digs near Wits?", "reddit_joburg", "r/Johannesburg", 4,
                                               kind="reddit")], [], now=NOW)
        self.assertIn("no news source (social chatter only)", cl[0]["rejects"])

    def test_low_value_filtered(self):
        cl = analyst.score_clusters(CFG, [item("Daily Lotto results: Saturday"), item("Lotto results: Saturday", "iol", "IOL")], [], now=NOW)
        self.assertEqual(cl, [])

    def test_trend_match_and_unavailable_flag(self):
        items = [item("Tyla wins big at the awards", "zalebs", "ZAlebs", 3)]
        cl = analyst.score_clusters(CFG, items, [{"platform": "X (Twitter)", "topic": "Tyla", "rank": 1, "volume": ""}], now=NOW)
        self.assertEqual(cl[0]["trend_hits"], ["X (Twitter): Tyla"])
        cl = analyst.score_clusters(CFG, items, [], now=NOW)
        self.assertEqual(cl[0]["trend_data"], "unavailable")

    def test_components_stored_and_weighted(self):
        cl = analyst.score_clusters(CFG, [item("NSFAS confirms 2027 student allowance increase")], [], now=NOW)
        comp = cl[0]["components"]
        self.assertEqual(set(comp), set(CFG["weights"]))
        self.assertAlmostEqual(cl[0]["score"], round(sum(CFG["weights"][k] * comp[k] for k in comp), 3), places=3)

    def test_non_sa_rejected_and_split_story_not_picked_twice(self):
        cl = analyst.score_clusters(CFG, [item("Haaland double helps Man City beat Ipswich")], [], now=NOW)
        self.assertIn("no clear South African angle", cl[0]["rejects"])
        cl = analyst.score_clusters(CFG, [item("Kaizer Chiefs lose to Red Arrows in Zambia", "kickoff", "KickOff", 2),
                                          item("Chiefs beaten by Red Arrows, Johnson under pressure", "citizen", "The Citizen", 2, hours_ago=20)],
                                    [], now=NOW)
        picks = analyst.pick_for_slot(CFG, cl, "evening", [], 5)
        self.assertEqual(sum("Red Arrows" in c["headline"] for c in picks), 1)

    def test_pick_skips_recent_topics(self):
        cl = analyst.score_clusters(CFG, [item("NSFAS confirms 2027 student allowance increase"),
                                          item("NSFAS 2027 allowance increase confirmed for students", "iol", "IOL")], [], now=NOW)
        used = [set(cl[0]["fingerprint"].split())]
        self.assertEqual(analyst.pick_for_slot(CFG, cl, "morning", used, 3), [])


def good_story(key):
    return {"cluster_key": key, "decision": "publish",
            "verification": {"status": "corroborated", "confidence": 0.9, "notes": "checked both outlets"},
            "risk": "low", "category": "student_funding", "tags": ["nsfas"], "primary_format": "poll",
            "headline": "NSFAS 2027 applications are open", "caption": "Applications close in January - here is what to know.",
            "body": ("NSFAS has opened applications for 2027 funding, according to SABC News and IOL. First-time "
                     "applicants and returning students who need to reapply can do it online. If you are in matric or "
                     "already studying and your household earns under the threshold, now is the time to get your "
                     "documents together."),
            "poll": {"question": "Have you started your NSFAS application?", "options": ["Done already", "This week", "Not yet"]},
            "formats": {}, "attribution": "Reporting: SABC News, IOL", "expires_hours": 24}


class TestEditor(unittest.TestCase):
    def setUp(self):
        cl = analyst.score_clusters(CFG, [item("NSFAS opens 2027 applications for students", "sabc", "SABC News",
                                                url="https://www.sabcnews.com/nsfas-2027"),
                                          item("NSFAS 2027 applications open for students", "iol", "IOL",
                                               url="https://iol.co.za/news/nsfas")], [], now=NOW)
        self.cluster = cl[0]

    def rec(self, **over):
        st = good_story(self.cluster["cluster_key"])
        st.update(over)
        return editor.build_record(CFG, self.cluster, st, "morning", "2026-10-17", NOW.isoformat())

    def test_good_post_auto_approves(self):
        errors, reasons = editor.review(CFG, self.rec())
        self.assertEqual((errors, reasons), ([], []))

    def test_copying_rejected(self):
        src = "the quick brown fox jumps over the lazy dog while students wait for nsfas to finally pay out the allowance"
        self.cluster["sources"][0]["summary"] = src
        errors, _ = editor.review(CFG, self.rec(body=src + " " + good_story("x")["body"]))
        self.assertTrue(any("copies" in e for e in errors))

    def test_tragedy_cannot_be_poll(self):
        self.cluster["tragedy"] = True
        r = self.rec()
        r["analyst"]["tragedy"] = True
        errors, _ = editor.review(CFG, r)
        self.assertTrue(any("tragedy" in e for e in errors))

    def test_risk_only_goes_up_and_high_risk_needs_review(self):
        r = self.rec(risk="low")
        r["analyst"]["risk"], r["analyst"]["risk_terms"] = "high", ["arrested"]
        _, reasons = editor.review(CFG, r)
        self.assertEqual(r["risk"], "high")
        self.assertTrue(any("high risk" in x for x in reasons))

    def test_low_confidence_and_allegation_need_review(self):
        _, reasons = editor.review(CFG, self.rec(verification={"status": "allegation", "confidence": 0.5, "notes": ""}))
        self.assertIn("story is an allegation", reasons)
        self.assertTrue(any("confidence" in x for x in reasons))

    def test_unverified_rejected_and_bad_poll_rejected(self):
        errors, _ = editor.review(CFG, self.rec(verification={"status": "unverified", "confidence": 0.2, "notes": ""}))
        self.assertTrue(any("verification" in e for e in errors))
        errors, _ = editor.review(CFG, self.rec(poll={"question": "Which one is it?", "options": ["Yes", "yes"]}))
        self.assertTrue(errors)

    def test_untrusted_domain_rejected(self):
        self.cluster["sources"][0]["url"] = "https://random-blog.example/post"
        errors, _ = editor.review(CFG, self.rec())
        self.assertTrue(any("domain" in e for e in errors))

    def test_feed_text_has_link_on_own_line_and_text_poll(self):
        txt = editor.feed_text(CFG, self.rec())
        self.assertIn("\nhttps://www.sabcnews.com/nsfas-2027\n", txt)
        self.assertIn("A) Done already", txt)

    def test_prefers_direct_link_over_google_news(self):
        self.cluster["sources"] = [{"outlet": "IOL", "title": "t", "url": "https://news.google.com/rss/articles/x", "summary": ""}]
        r = self.rec(verification={"status": "corroborated", "confidence": 0.9, "notes": "",
                                   "extra_sources": [{"outlet": "SABC News", "url": "https://www.sabcnews.com/x"}]})
        self.assertEqual(r["source_url"], "https://www.sabcnews.com/x")

    def test_writer_link_choice_used_only_if_known_source(self):
        self.assertEqual(self.rec(link_url="https://iol.co.za/news/nsfas")["source_url"], "https://iol.co.za/news/nsfas")
        self.assertNotEqual(self.rec(link_url="https://evil.example/x")["source_url"], "https://evil.example/x")

    def test_bot_routing(self):
        self.assertEqual(editor.bot_for(CFG, "sport_football"), "lwazi.bot@campusplug.com")
        self.assertEqual(editor.bot_for(CFG, "student_funding"), "ayanda.bot@campusplug.com")
        self.assertEqual(editor.bot_for(CFG, "unknown"), "naledi.bot@campusplug.com")

    def test_slot_times_spaced_and_future(self):
        times = editor.slot_times(CFG, "morning", 3, now=NOW)
        ts = [datetime.fromisoformat(t) for t in times]
        self.assertGreater(ts[0], NOW)
        self.assertEqual((ts[1] - ts[0]).seconds, CFG["space_minutes"] * 60)


class TestAutoPolicy(unittest.TestCase):
    def setUp(self):
        self.t = TestEditor()
        self.t.setUp()

    def test_tragedy_allegation_high_risk_always_dropped(self):
        for mut in ({"tragedy": True}, {"risk": "high"}):
            r = self.t.rec()
            r["analyst"].update(mut)
            if "risk" in mut:
                r["risk"] = "high"
            self.assertFalse(editor.auto_decide(CFG, r)[0])
        r = self.t.rec(verification={"status": "allegation", "confidence": 0.99, "notes": ""})
        self.assertFalse(editor.auto_decide(CFG, r)[0])

    def test_single_source_money_dropped_but_sport_allowed(self):
        r = self.t.rec(verification={"status": "single_source", "confidence": 0.9, "notes": ""})
        r["analyst"]["outlets"] = ["sabcnews"]
        self.assertFalse(editor.auto_decide(CFG, r)[0])  # student_funding
        r["category"] = "sport_football"
        self.assertTrue(editor.auto_decide(CFG, r)[0])
        r["confidence"] = 0.8
        self.assertFalse(editor.auto_decide(CFG, r)[0])

    def test_medium_risk_needs_neutral_format(self):
        r = self.t.rec(risk="medium")
        self.assertFalse(editor.auto_decide(CFG, r)[0])  # poll
        r["format"] = "explainer"
        self.assertTrue(editor.auto_decide(CFG, r)[0])

    def _audit(self, reply=None, error=None, writer="groq", avail=("groq", "gemini")):
        from engine import auditor, fetch, llm
        rec = self.t.rec()
        def fake(provider, *a, **k):
            if error:
                raise llm.LLMError(error)
            return reply, {"provider": provider, "model": a[1], "seconds": 0, "searches": 0}
        with mock.patch.object(llm, "available", side_effect=lambda p: p in avail), \
                mock.patch.object(llm, "call", side_effect=fake) as call, \
                mock.patch.object(llm, "log_usage"), mock.patch.object(fetch, "evidence", return_value=[]), \
                mock.patch.object(fetch, "news_search", return_value=[]), \
                mock.patch("time.sleep"), tempfile.TemporaryDirectory() as d:
            out = auditor.run(CFG, "morning", "2026-10-17", [rec], lambda m: None, {rec["post_id"]: writer},
                              out_dir=Path(d))
        return out.get(rec["post_id"]), call

    def test_auditor_fails_closed(self):
        self.assertIsNone(self._audit(error="HTTP 429")[0])
        self.assertIsNone(self._audit(reply="sorry, cannot help")[0])
        self.assertFalse(self._audit(reply='{"pass": "yes", "reason": "x"}')[0][0])  # only literal true passes

    def test_auditor_is_a_different_model(self):
        verdict, call = self._audit(reply='{"pass": true, "reason": "facts match"}', writer="groq")
        self.assertTrue(verdict[0])
        self.assertEqual(call.call_args[0][0], "gemini")
        verdict, call = self._audit(reply='{"pass": true}', writer="gemini", avail=("gemini",))
        self.assertIsNone(verdict)  # only the writer's model is available -> no independent audit -> fail
        call.assert_not_called()

    def test_creator_needs_free_keys(self):
        from engine import creator, llm
        with mock.patch.object(llm, "available", return_value=False):
            self.assertIsNone(creator.run(CFG, "morning", "2026-10-17", [{"cluster_key": "k"}], lambda m: None))

    def test_chain_falls_back_and_cools_down(self):
        from engine import llm
        tried = []
        def fake(provider, prompt, model, **k):
            tried.append(model)
            if model == "openai/gpt-oss-120b":
                raise llm.RateLimited("HTTP 429")
            return '{"ok": 1}', {"model": model}
        llm._cooldown.clear()
        with mock.patch.object(llm, "call", side_effect=fake):
            text, meta = llm.call_chain(CFG, "groq", "audit", lambda m: "p", lambda m: None)
            self.assertEqual(meta["model"], "openai/gpt-oss-20b")
            llm.call_chain(CFG, "groq", "audit", lambda m: "p", lambda m: None)
        self.assertEqual(tried, ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-20b"])  # skipped while cooling
        llm._cooldown.clear()

    def test_weak_models_never_audit(self):
        from engine import llm
        audit = [m["model"] for p in ("groq", "gemini") for m in llm.chain(CFG, p, "audit")]
        self.assertTrue(audit)
        self.assertFalse([m for m in audit if "lite" in m or "gemma" in m or "qwen" in m])

    def test_community_validation_and_feed_text(self):
        from engine import community
        good = {"format": "this_or_that", "headline": "Past papers or neat notes?",
                "body": "Exam season energy check. Some swear by past papers, some by colour-coded notes. Which one actually works for you? Drop it below.",
                "poll": {"question": "Your weapon?", "options": ["Past papers", "Neat notes"]}}
        self.assertTrue(community._valid(good))
        self.assertFalse(community._valid({**good, "poll": {"options": ["Same", "same"]}}))  # duplicate options
        self.assertFalse(community._valid({**good, "format": "news"}))
        text = community.feed_text({**good, "tag": "#StudyTok"})
        self.assertIn("A) Past papers", text)
        self.assertTrue(text.endswith("#CampusPlug #StudyTok"))

    def test_community_slots_inside_window(self):
        from engine import community
        from zoneinfo import ZoneInfo
        sast = ZoneInfo("Africa/Johannesburg")
        early = datetime.now(sast).replace(hour=6, minute=0)
        times = [datetime.fromisoformat(t).astimezone(sast) for t in community.slot_times(CFG, "am", 10, now=early)]
        self.assertEqual(len(times), 10)
        self.assertTrue(all(7 <= t.hour < 14 for t in times))
        late = datetime.now(sast).replace(hour=22, minute=0)
        self.assertEqual(community.slot_times(CFG, "pm", 5, now=late), [])  # never schedules into quiet hours

    def test_groq_citations_stripped(self):
        from engine import llm
        self.assertEqual(llm.CITE_RE.sub("", "Chiefs lost 1-0 【1†L3-L10】."), "Chiefs lost 1-0.")

    def test_parse_json_from_chatty_reply(self):
        from engine import llm
        self.assertEqual(llm.parse_json('Here you go:\n```json\n{"pass": true, "reason": "ok"}\n```')["pass"], True)
        with self.assertRaises(llm.LLMError):
            llm.parse_json("no json here")

    def test_extract_article_text(self):
        from engine import fetch
        page = ('<html><head><meta property="og:title" content="Fort Hare names VC"></head><body><nav><p>'
                + "menu " * 30 + '</p></nav><p>' + "The council unanimously appointed the new vice-chancellor. " * 3
                + '</p><p>Subscribe to our newsletter for more news and updates every single day of the week.</p></body>')
        got = fetch.extract(page, 2000)
        self.assertEqual(got["title"], "Fort Hare names VC")
        self.assertIn("unanimously appointed", got["text"])
        self.assertNotIn("menu", got["text"])
        self.assertNotIn("newsletter", got["text"])


class TestPublisher(unittest.TestCase):
    def test_idempotent_key_and_retry(self):
        con = db.connect(Path(":memory:")) if False else sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(db.SCHEMA)
        t = TestEditor()
        t.setUp()
        rec = t.rec()
        rec["bot"] = "ayanda.bot@campusplug.com"
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        db.save_post(con, rec, "scheduled", past)
        calls = []

        def fake_rpc(env, name, payload, timeout=30):
            calls.append(payload)
            if len(calls) == 1:
                raise RuntimeError("post_engagement: HTTP 503 upstream")
            return "posted"
        env = {"CAMPUSPLUG_TREND_BOT_TOKEN": "t"}
        with mock.patch.object(publisher, "rpc", fake_rpc), mock.patch.object(publisher, "load_env", lambda: env):
            self.assertEqual(publisher.publish_due(CFG, con, lambda m: None), 0)  # first try fails, stays scheduled
            self.assertEqual(con.execute("select status from posts").fetchone()[0], "scheduled")
            self.assertEqual(publisher.publish_due(CFG, con, lambda m: None), 1)
        self.assertEqual(calls[0]["p_dedupe_key"], calls[1]["p_dedupe_key"])
        self.assertIsNone(calls[1]["p_poll_options"])  # native polls off until the app format is confirmed
        self.assertEqual(con.execute("select status from posts").fetchone()[0], "published")


if __name__ == "__main__":
    unittest.main()
