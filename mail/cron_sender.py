"""Shared sender for the cron emails (Social Radar, Opportunities, Daily Brief).

All three must come from campusplug.qconnec@gmail.com. A Gmail From address is only
deliverable when the mail goes through Google's own SMTP, authenticated as that
account, so this logs in to smtp.gmail.com with an app password stored in
gmail_credentials.env (written by gmail_setup.py, mode 600).

Until that file exists, mail still goes out through the GoDaddy mailbox (old From
address) and each send logs a warning, so the emails never just stop. Once Gmail is
configured there is NO fallback: a Gmail failure is logged as a failure, never
silently re-sent from another address.
"""
import smtplib
import ssl
from email.utils import formataddr, parseaddr
from pathlib import Path

from mailer import load_creds

GMAIL_ADDRESS = "campusplug.qconnec@gmail.com"
GMAIL_CRED = Path(__file__).with_name("gmail_credentials.env")


def gmail_configured():
    return GMAIL_CRED.exists()


def _read_env(path):
    cfg = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def send(msg, log=print):
    """Send an EmailMessage. Rewrites From to the Gmail account when configured.

    Returns a short label of the route used ("gmail" / "godaddy-fallback").
    Raises on SMTP failure so each caller keeps its own failure logging.
    """
    ctx = ssl.create_default_context()
    if gmail_configured():
        cfg = _read_env(GMAIL_CRED)
        name, _ = parseaddr(msg["From"] or "")
        del msg["From"]
        msg["From"] = formataddr((name or "Campus Plug", cfg.get("GMAIL_USER", GMAIL_ADDRESS)))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=60) as s:
            s.login(cfg.get("GMAIL_USER", GMAIL_ADDRESS), cfg["GMAIL_APP_PASSWORD"])
            s.send_message(msg)
        return "gmail"

    log(f"  WARNING: {GMAIL_CRED.name} missing; sent via GoDaddy as {msg['From']}. "
        f"Run ~/claude-code/qconnec/qconnec-mail/gmail_setup.py to switch to {GMAIL_ADDRESS}.")
    creds = load_creds()
    port = int(creds["SMTP_PORT"])
    if port == 465:
        with smtplib.SMTP_SSL(creds["SMTP_HOST"], port, context=ctx, timeout=60) as s:
            s.login(creds["SMTP_USER"], creds["SMTP_PASS"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(creds["SMTP_HOST"], port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(creds["SMTP_USER"], creds["SMTP_PASS"])
            s.send_message(msg)
    return "godaddy-fallback"
