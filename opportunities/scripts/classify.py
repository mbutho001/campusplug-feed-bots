"""Turn a raw listing into a normalised Campus Plug opportunity record.

Everything here is extraction, never invention: a field that cannot be read
off the source text stays None, so a missing closing date is visible as
missing rather than guessed. Campus Plug publishes these to students, and a
wrong closing date costs someone an application.
"""
import re
from datetime import date, datetime

CATEGORIES = ["bursary", "internship", "learnership", "apprenticeship",
              "in-service training", "graduate programme"]

# Ordered: the first pattern that hits wins, so the more specific terms lead.
_CATEGORY_RULES = [
    ("in-service training", r"in[-\s]?service\s+train|experiential\s+(?:learn|train)|"
                           r"work[-\s]?integrated\s+learning|\bwil\b|p1\s*/?\s*p2|\bp[12]\s+student"),
    ("apprenticeship",     r"apprentice"),
    ("bursary",            r"bursar|scholarship|study\s+grant|funding\s+opportunit|"
                           r"tuition\s+(?:support|assistance)"),
    ("learnership",        r"learnership|\blearner\s+programme|yes\s+programme|"
                           r"yes4youth|youth\s+employment\s+service"),
    ("graduate programme", r"graduate\s+(?:programme|program|development|trainee)|"
                           r"grad\s+programme|trainee\s+programme|management\s+trainee"),
    ("internship",         r"internship|\bintern\b|\binterns\b|vacation\s+work|vac\s+work"),
]

PROVINCES = {
    "Gauteng":       r"gauteng|johannesburg|joburg|jhb|pretoria|tshwane|sandton|midrand|soweto|centurion|vereeniging|ekurhuleni|benoni|kempton park|randburg|roodepoort|germiston|springs\b|vanderbijlpark",
    "Western Cape":  r"western cape|cape town|stellenbosch|bellville|paarl|worcester|george\b|somerset west|khayelitsha|saldanha|mossel bay",
    "KwaZulu-Natal": r"kwazulu|kzn|durban|pietermaritzburg|umhlanga|richards bay|newcastle|pinetown|ballito|empangeni",
    "Eastern Cape":  r"eastern cape|gqeberha|port elizabeth|east london|mthatha|umtata|queenstown|uitenhage|kariega|bhisho",
    "Free State":    r"free state|bloemfontein|welkom|sasolburg|bethlehem|qwaqwa|kroonstad",
    "Limpopo":       r"limpopo|polokwane|thohoyandou|tzaneen|mokopane|lephalale|musina|giyani",
    "Mpumalanga":    r"mpumalanga|nelspruit|mbombela|witbank|emalahleni|secunda|middelburg|ermelo|standerton",
    "North West":    r"north west|rustenburg|mahikeng|mafikeng|potchefstroom|klerksdorp|brits\b|vryburg",
    "Northern Cape": r"northern cape|kimberley|upington|springbok|kathu|de aar",
}

FIELDS = {
    "Engineering":        r"engineer|mechatron|electrical|mechanical|civil|chemical|metallurg|mining\b",
    "IT & Data":          r"\bit\b|information technology|software|develop(?:er|ment)|data\s|cyber|computer science|informatics|programming|network",
    "Finance & Accounting": r"account|financ|audit|\bcima\b|\bsaica\b|\bsaipa\b|actuar|bookkeep|tax\b|investment",
    "Business & Admin":   r"business admin|office admin|administrat|management|\bhr\b|human resource|procurement|supply chain|logistic|operations",
    "Health & Science":   r"nurs|medic|pharmac|health|clinical|laborator|biotech|chemistry|physic|radiograph|dietet",
    "Law":                r"\blaw\b|legal|\bllb\b|paralegal|attorney|advocate|justice",
    "Marketing & Media":  r"market|media\b|journal|communicat|public relations|advertis|graphic design|digital media|broadcast",
    "Education":          r"teach|education|lectur|early childhood|\bece\b",
    "Trades & Technical": r"artisan|fitter|boilermak|welding|millwright|diesel mechanic|plumb|electrician|technician",
    "Agriculture":        r"agricultur|farm|horticultur|agronom|animal science|forestr",
    "Hospitality & Tourism": r"hospitality|tourism|chef|culinar|hotel|catering",
}

_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december"
           "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")

# Only patterns anchored on an explicit closing word are trusted - a bare date
# in the body is just as likely to be a start date or a publication date.
_CLOSE_ANCHOR = (r"(?:clos(?:ing|es|e|ed)?|deadline|apply\s+(?:by|before)|"
                 r"applications?\s+clos\w*|last\s+day\s+to\s+apply|submit\s+by|due)")

_DATE_PATTERNS = [
    rf"{_CLOSE_ANCHOR}[^\n]{{0,40}}?\b(\d{{1,2}})\s+({_MONTHS})\.?\s+(\d{{4}})",
    rf"{_CLOSE_ANCHOR}[^\n]{{0,40}}?\b({_MONTHS})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})",
    rf"{_CLOSE_ANCHOR}[^\n]{{0,40}}?\b(\d{{1,2}})[/-](\d{{1,2}})[/-](\d{{4}})",
]

_MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _mk(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def closing_date(*texts):
    """Read an explicit closing date. Returns (date|None, verbatim_snippet|None)."""
    blob = "\n".join(t for t in texts if t)
    for idx, pat in enumerate(_DATE_PATTERNS):
        m = re.search(pat, blob, re.I)
        if not m:
            continue
        a, b, c = m.group(1), m.group(2), m.group(3)
        if idx == 0:
            got = _mk(c, _MONTH_NUM.get(b.lower()[:3]), a)
        elif idx == 1:
            got = _mk(c, _MONTH_NUM.get(a.lower()[:3]), b)
        else:
            got = _mk(c, b, a)   # SA convention is day/month/year
        if got:
            return got, re.sub(r"\s+", " ", m.group(0)).strip()
    return None, None


def _match_categories(blob):
    return [name for name, pat in _CATEGORY_RULES if re.search(pat, blob, re.I)]


def categorise(title, body, categories):
    """Return every category the listing matches, most specific first.

    The title is consulted alone first. Listing sites file a programme under
    whichever index page it was scraped from, so an internship reached via a
    /learnerships page arrives tagged "learnership" - the title is the more
    reliable witness, and only a title that says nothing falls back to the
    body and the site's own tags.
    """
    hits = _match_categories(title or "")
    if hits:
        return hits
    return _match_categories(" ".join([" ".join(categories or []), (body or "")[:1500]]))


def province(*texts):
    blob = " ".join(t for t in texts if t).lower()
    found = [name for name, pat in PROVINCES.items() if re.search(pat, blob, re.I)]
    return found[0] if len(found) == 1 else ("Multiple provinces" if found else None)


def fields(title, body):
    blob = f"{title or ''} {(body or '')[:1200]}".lower()
    return [name for name, pat in FIELDS.items() if re.search(pat, blob, re.I)]


_PROGRAMME_WORDS = re.compile(
    r"\b(bursar\w*|scholarship|internship\w*|intern|learnership\w*|apprentice\w*|"
    r"graduate|trainee|programme|program|vacanc\w*|opportunit\w*|recruitment|"
    r"in[-\s]?service|vacation\s+work|\d{4}(?:/\d{2,4})?)\b", re.I)


def employer(title):
    """Employers are usually the part before a colon or dash in these feeds.

    Titles vary between "Eskom: Bursary 2027" and "DFFE Bursary 2027: 20 Posts",
    so the candidate is cut at the first programme-type word - otherwise the
    whole programme name is reported as the employer.
    """
    if not title:
        return None
    candidate = None
    m = re.match(r"\s*([A-Z][^:|]{2,60}?)\s*[:|]\s*\S", title)
    if m:
        candidate = m.group(1)
    else:
        m = re.match(r"\s*([A-Z][\w&'.\- ]{2,45}?)\s+[-–—]\s+\S", title)
        if m:
            candidate = m.group(1)
        else:
            candidate = title      # no separator: still try to read a leading name
    cut = _PROGRAMME_WORDS.search(candidate)
    if cut:
        candidate = candidate[:cut.start()]
    candidate = candidate.strip(" -–—:,&")
    # Two to six words of a proper name; anything else is not an employer.
    if not (2 <= len(candidate) <= 45) or len(candidate.split()) > 6:
        return None
    return candidate or None


def matric_only(title, body):
    """Grade 12 / no-experience openings - the widest-reach posts for the app."""
    blob = f"{title or ''} {(body or '')[:1200]}".lower()
    return bool(re.search(r"grade\s*12|matric|no\s+experience|nqf\s*level\s*4\b", blob))


def days_left(close, today=None):
    if not close:
        return None
    return (close - (today or date.today())).days


def is_stale(published, max_age_days, today=None):
    """True when a listing is older than the window we publish from."""
    dt = parse_pubdate(published)
    if not dt:
        return False          # unknown age is not evidence of staleness
    return (today or date.today()) - dt > _td(max_age_days)


def _td(days):
    from datetime import timedelta
    return timedelta(days=days)


_PUB_FORMATS = ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y")


def parse_pubdate(value):
    if not value:
        return None
    value = value.strip()
    for fmt in _PUB_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    m = re.search(rf"(\d{{1,2}})\s+({_MONTHS})\.?\s+(\d{{4}})", value, re.I)
    if m:
        return _mk(m.group(3), _MONTH_NUM.get(m.group(2).lower()[:3]), m.group(1))
    return None
