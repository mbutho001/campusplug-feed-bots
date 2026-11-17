"""South African retail calendar for the Plug Store (student market).

season_for(day) -> dict describing how the store should behave that day:
  name, theme        - used in feed copy
  slots              - how many Plug Deals run at once (15 % markup instead of 20 %)
  deal_days          - how long one deal round lasts
  gap_days           - rest days between rounds ("intermittent")
  focus              - category weights for picking deal products (iPhone, Mac, iPad, Audio, Apple Watch, Accessories)
  kickoff            - feed copy posted once on the first day the season is seen

Payday weeks (25th to 2nd: salaries, then NSFAS/bursary allowances) add one deal slot.
Dates are fixed for 2026-2027. Easter moves, so it is listed per year.
"""
from __future__ import annotations

from datetime import date

EASTER = {2026: date(2026, 4, 5), 2027: date(2027, 3, 28), 2028: date(2028, 4, 16)}

BASE = {"iPhone": 1.0, "Mac": 1.0, "iPad": 1.0, "Audio": 1.0, "Apple Watch": 1.0, "Accessories": 0.6}


def _s(name, theme, slots, deal_days, gap_days, focus, kickoff):
    f = dict(BASE)
    f.update(focus)
    return {"name": name, "theme": theme, "slots": slots, "deal_days": deal_days, "gap_days": gap_days,
            "focus": f, "kickoff": kickoff}


def season_for(d: date) -> dict:
    y, m, day = d.year, d.month, d.day
    easter = EASTER.get(y)
    s = None
    if m == 11:
        s = _s("black-november", "Black November", 4, 4, 1, {"Mac": 1.6, "iPad": 1.5, "Audio": 1.3},
               "Black November is on in the Plug Store: extra Plug Deals on Apple all month, while stocks last.")
    elif m == 12 and day <= 25:
        s = _s("festive-gifting", "festive gifting", 3, 5, 1, {"Audio": 1.8, "Apple Watch": 1.6, "Accessories": 1.2, "iPhone": 1.2},
               "Festive season gifting: AirPods, Apple Watch and iPhone Plug Deals for the people who carried you through exams.")
    elif (m == 12 and day >= 26) or (m == 1 and day <= 5):
        s = _s("year-end-sales", "year-end sales", 3, 4, 1, {"iPhone": 1.4, "Audio": 1.3},
               "Year-end sales: Plug Deals on Apple until 5 January. Use that festive money well.")
    elif (m == 1 and day >= 6) or m == 2:
        s = _s("back-to-campus", "back to campus", 4, 5, 1, {"Mac": 2.0, "iPad": 1.8, "Accessories": 1.3},
               "Back to campus: MacBook, iPad and study-kit Plug Deals through February. Get set up before lectures start.")
        if m == 2 and day <= 14:
            s["focus"]["Audio"] = 1.5
            s["focus"]["Apple Watch"] = 1.4
    elif easter and 0 <= (easter - d).days <= 21 or (easter and 0 < (d - easter).days <= 2):
        s = _s("easter", "Easter long weekend", 2, 4, 3, {"Audio": 1.4, "iPad": 1.3},
               "Easter weekend Plug Deals on Apple: treat yourself before the second half of the semester.")
    elif m in (5,) or (m == 6 and day < 10):
        s = _s("mid-year-exams", "exam season", 2, 5, 4, {"iPad": 1.6, "Mac": 1.4, "Accessories": 1.3},
               "Exam season: Plug Deals on iPad, Pencil and MacBook to get you through the June papers.")
    elif m == 6:
        s = _s("youth-month", "Youth Month", 3, 4, 2, {"iPhone": 1.5, "Audio": 1.4},
               "Youth Month in the Plug Store: Plug Deals on iPhone and AirPods for 16 June and beyond.")
    elif m == 7:
        s = _s("winter-break", "mid-year break", 2, 5, 3, {"Mac": 1.4, "iPad": 1.4},
               "Mid-year break Plug Deals: upgrade before second semester starts.")
    elif m == 8:
        s = _s("womens-month", "Women's Month", 2, 5, 3, {"Apple Watch": 1.5, "Audio": 1.4, "iPhone": 1.2},
               "Women's Month Plug Deals on Apple Watch, AirPods and iPhone.")
    elif m == 9:
        s = _s("heritage-spring", "spring & Heritage Month", 2, 4, 3, {"iPhone": 1.6, "Audio": 1.2},
               "Spring is here, and so are new iPhones. Heritage Month Plug Deals on Apple.")
    elif m == 10:
        s = _s("pre-black-friday", "early Black Friday", 2, 4, 3, {"Mac": 1.3, "iPad": 1.3, "iPhone": 1.2},
               "Early Black Friday Plug Deals: no need to wait for November.")
    else:   # 3, 4 (outside Easter), early 12/25
        s = _s("semester", "semester", 2, 4, 4, {},
               "New Plug Deals on Apple in the Plug Store this week.")
    if day >= 25 or day <= 2:
        s["slots"] += 1
        s["payday"] = True
    return s
