#!/usr/bin/env python3
"""Send mail from a qconnec.co.za address via GoDaddy SMTP.

Authenticates as the real mailbox (SMTP_USER) and puts an alias in the From
header. Aliases must exist on the mailbox in the GoDaddy Email & Office portal.

Credentials live in ~/claude-code/qconnec/qconnec-mail/credentials.env (chmod 600), written by setup.py.
"""
import argparse, mimetypes, os, smtplib, ssl, sys
from email.message import EmailMessage
from pathlib import Path

CRED = Path.home() / "claude-code" / "qconnec" / "qconnec-mail" / "credentials.env"


def load_creds():
    if not CRED.exists():
        sys.exit(f"No credentials at {CRED}. Run: python3 {Path(__file__).with_name('setup.py')}")
    cfg = {}
    for line in CRED.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
    for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"):
        if not cfg.get(key):
            sys.exit(f"{key} missing from {CRED}")
    cfg["ALIASES"] = [a.strip() for a in cfg.get("ALIASES", "").split(",") if a.strip()]
    cfg.setdefault("DEFAULT_FROM", cfg["SMTP_USER"])
    return cfg


def resolve_from(cfg, requested, allow_any):
    """Pick the From address and check it is a known alias of the mailbox."""
    addr = requested or cfg["DEFAULT_FROM"] or cfg["SMTP_USER"]
    known = [cfg["SMTP_USER"]] + cfg["ALIASES"]
    if addr not in known and not allow_any:
        sys.exit(
            f"{addr!r} is not the mailbox or a recorded alias.\n"
            f"Known: {', '.join(known)}\n"
            f"Add it in the GoDaddy portal and rerun setup.py, or pass --allow-any-from."
        )
    return addr


def build(cfg, args):
    msg = EmailMessage()
    sender = resolve_from(cfg, args.from_address, args.allow_any_from)
    msg["From"] = f"{args.from_name} <{sender}>" if args.from_name else sender
    msg["To"] = ", ".join(args.to)
    if args.cc:
        msg["Cc"] = ", ".join(args.cc)
    if args.reply_to:
        msg["Reply-To"] = args.reply_to
    msg["Subject"] = args.subject

    body = Path(args.body_file).read_text() if args.body_file else args.body
    if args.html:
        html = Path(args.html).read_text() if os.path.exists(args.html) else args.html
        msg.set_content(body or "This message requires an HTML-capable mail client.")
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(body)

    for path in args.attach or []:
        p = Path(path).expanduser()
        if not p.is_file():
            sys.exit(f"Attachment not found: {p}")
        ctype, _ = mimetypes.guess_type(str(p))
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    return msg


def main():
    ap = argparse.ArgumentParser(description="Send mail as contact@qconnec.co.za")
    ap.add_argument("--to", nargs="+", required=True)
    ap.add_argument("--cc", nargs="*")
    ap.add_argument("--bcc", nargs="*")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", help="plain-text body")
    ap.add_argument("--body-file", help="file containing the plain-text body")
    ap.add_argument("--html", help="HTML body, inline or a path to an .html file")
    ap.add_argument("--attach", nargs="*", help="file paths to attach")
    ap.add_argument("--from-address", help="alias to send as; defaults to DEFAULT_FROM")
    ap.add_argument("--allow-any-from", action="store_true",
                    help="skip the alias check (GoDaddy may still reject the sender)")
    ap.add_argument("--from-name", default="Qconnec")
    ap.add_argument("--reply-to")
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    args = ap.parse_args()

    if not (args.body or args.body_file or args.html):
        sys.exit("Provide --body, --body-file or --html")

    cfg = load_creds()
    msg = build(cfg, args)
    rcpts = list(args.to) + list(args.cc or []) + list(args.bcc or [])

    if args.dry_run:
        print(msg)
        print(f"[dry-run] auth as: {cfg['SMTP_USER']}")
        print(f"\n[dry-run] would send to: {', '.join(rcpts)}")
        return

    port = int(cfg["SMTP_PORT"])
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=30)
        server.starttls(context=ctx)
    envelope = msg["From"].split("<")[-1].strip(">").strip()
    with server:
        server.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
        try:
            server.send_message(msg, from_addr=envelope, to_addrs=rcpts)
        except smtplib.SMTPSenderRefused as e:
            # Some GoDaddy mailboxes only accept the authenticated address as the
            # envelope sender. Retry with it; the alias stays in the From header.
            print(f"Envelope {envelope} refused ({e.smtp_code}); retrying as {cfg['SMTP_USER']}")
            server.send_message(msg, from_addr=cfg["SMTP_USER"], to_addrs=rcpts)
    print(f"Sent as {msg['From']}: {args.subject!r} -> {', '.join(rcpts)}")


if __name__ == "__main__":
    main()
