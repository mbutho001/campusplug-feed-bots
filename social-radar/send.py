"""Send the digest through the existing GoDaddy SMTP mailbox."""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

sys.path.insert(0, str(Path.home() / "claude-code" / "qconnec" / "qconnec-mail"))
from cron_sender import send as deliver_mail  # noqa: E402  Gmail sender shared by all cron emails


def send(cfg, subject, html_body, text_body, images, log):
    """images: list of (cid, jpeg_bytes). Returns True on a delivered message."""
    msg = EmailMessage()
    msg["From"] = f'{cfg["from_name"]} <{cfg["from_address"]}>'
    # Recipients come from the DIGEST_RECIPIENTS secret on GitHub (config.json is public).
    cfg["recipients"] = cfg["recipients"] or [a.strip() for a in os.environ.get("DIGEST_RECIPIENTS", "").split(",") if a.strip()]
    msg["To"] = ", ".join(cfg["recipients"])
    msg["Subject"] = subject
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    payload = msg.get_payload()[1]
    for cid, blob in images:
        payload.add_related(blob, "image", "jpeg", cid=f"<{cid}>")

    try:
        route = deliver_mail(msg, log)
    except Exception as exc:  # noqa: BLE001 - cron needs the reason in the log, not a traceback
        log(f"  smtp: FAILED {exc}")
        return False
    log(f"  smtp: delivered to {', '.join(cfg['recipients'])} via {route}")
    return True


def new_cid():
    return make_msgid(domain="campusplug.local")[1:-1]
