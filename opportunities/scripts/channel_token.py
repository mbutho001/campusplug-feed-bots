#!/usr/bin/env python3
"""Connect the YouTube channel or the TikTok account to channel_post.py (run it yourself).

Values are typed hidden and go straight into GitHub secrets; nothing is printed or saved.

    python3 opportunities/scripts/channel_token.py youtube
        asks: OAuth client ID + secret (Google Cloud -> APIs & Services -> Credentials,
              type "Desktop app", YouTube Data API v3 enabled, consent screen "In production"
              - in "Testing" Google expires the token after 7 days)
        opens Google sign-in: pick the Campus Plug | Student App SA channel
        sets: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

    python3 opportunities/scripts/channel_token.py tiktok
        asks: Client key + secret (developers.tiktok.com -> your app; products Login Kit +
              Content Posting API with Direct Post on; redirect URI https://campusplug.club/)
        opens TikTok sign-in: log in as @campusplug_sa, approve, then paste the address-bar
              URL you land on (https://campusplug.club/?code=...)
        sets: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN

Needs the gh CLI logged in with access to mbutho001/campusplug-feed-bots.
"""
import getpass
import os
from pathlib import Path
import http.server
import json
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REPO = "mbutho001/campusplug-feed-bots"
TIKTOK_REDIRECT = "https://campusplug.club/"


def post_form(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"token exchange failed: {exc.read()[:300].decode('utf-8', 'replace')}")


def set_secret(name, value):
    subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value.encode(), check=True)
    print(f"{name} saved to {REPO}")


def youtube():
    cid = input("OAuth client ID: ").strip()
    csecret = getpass.getpass("OAuth client secret (hidden): ").strip()
    got = {}

    class Catch(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Campus Plug: YouTube connected. You can close this tab.</h2>")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Catch)
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    state = secrets.token_urlsafe(12)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": state,
        "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        "access_type": "offline", "prompt": "consent"})
    print("\nOpening Google sign-in. Choose the Campus Plug channel when asked.\nIf no browser opens, go to:\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if got.get("state", [""])[0] != state or "code" not in got:
        sys.exit(f"Google sign-in did not finish: {got.get('error')}")
    tok = post_form("https://oauth2.googleapis.com/token", {
        "code": got["code"][0], "client_id": cid, "client_secret": csecret,
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    if "refresh_token" not in tok:
        sys.exit("Google gave no refresh token - remove the app at myaccount.google.com/permissions and run again")
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": f"Bearer {tok['access_token']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        names = [c["snippet"]["title"] for c in json.loads(r.read()).get("items", [])]
    print("YouTube channel:", ", ".join(names) or "NONE - sign in again and pick the channel")
    set_secret("YT_CLIENT_ID", cid)
    set_secret("YT_CLIENT_SECRET", csecret)
    set_secret("YT_REFRESH_TOKEN", tok["refresh_token"])


def tiktok():
    key = input("TikTok client key: ").strip()
    csecret = getpass.getpass("TikTok client secret (hidden): ").strip()
    state = secrets.token_urlsafe(12)
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
        "client_key": key, "response_type": "code", "redirect_uri": TIKTOK_REDIRECT, "state": state,
        "scope": "user.info.basic,video.publish,video.upload"})
    print("\nOpening TikTok sign-in. Log in as @campusplug_sa and approve.\nIf no browser opens, go to:\n" + url)
    webbrowser.open(url)
    back = getpass.getpass("\nPaste the full address you land on (https://campusplug.club/?code=...) (hidden): ").strip()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
    if q.get("state", [""])[0] != state or "code" not in q:
        sys.exit("That address has no code from this sign-in - run it again")
    tok = post_form("https://open.tiktokapis.com/v2/oauth/token/", {
        "client_key": key, "client_secret": csecret, "code": q["code"][0],
        "grant_type": "authorization_code", "redirect_uri": TIKTOK_REDIRECT})
    if "refresh_token" not in tok:
        sys.exit(f"TikTok gave no refresh token: {tok.get('error_description') or tok}")
    print(f"TikTok: connected (scopes {tok.get('scope')})")
    set_secret("TIKTOK_CLIENT_KEY", key)
    set_secret("TIKTOK_CLIENT_SECRET", csecret)
    set_secret("TIKTOK_REFRESH_TOKEN", tok["refresh_token"])


def x():
    """X developer portal -> your app -> Keys and tokens. App permissions must be Read and write
    BEFORE you generate the Access Token, or posts fail with 403."""
    print("developer.x.com -> Projects & Apps -> your app -> Keys and tokens (signed in as @CampusPlug_SA).")
    vals = {n: getpass.getpass(f"{label} (hidden): ").strip() for n, label in (
        ("X_API_KEY", "API Key (consumer key)"), ("X_API_SECRET", "API Key Secret"),
        ("X_ACCESS_TOKEN", "Access Token"), ("X_ACCESS_SECRET", "Access Token Secret"))}
    if not all(vals.values()):
        sys.exit("One of the four values was empty - nothing saved.")
    os.environ.update(vals)
    import pro_post
    print(pro_post.x_check())          # proves the keys work before saving them
    for n, v in vals.items():
        set_secret(n, v)
    print("Saved. X posts go out at 13:00 and 20:30.")


def linkedin():
    """LinkedIn app "Campus Plug Publisher" (264231871): Auth tab -> Client ID / Primary Client Secret.
    Works only after LinkedIn approves the Community Management API for the app."""
    cid = input("LinkedIn Client ID: ").strip()
    csec = getpass.getpass("LinkedIn Client Secret (hidden): ").strip()
    state = secrets.token_urlsafe(16)
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": "https://campusplug.club/", "state": state,
        "scope": "w_organization_social r_organization_social rw_organization_admin"})
    print("\nOpen this, sign in as the Campus Plug page admin and click Allow:\n" + url)
    webbrowser.open(url)
    back = getpass.getpass("\nPaste the full address you land on (https://campusplug.club/?code=...) (hidden): ").strip()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(back).query))
    if q.get("state") != state or "code" not in q:
        sys.exit(f"No valid code in that address ({q.get('error_description') or 'state mismatch'}).")
    tok = post_form("https://www.linkedin.com/oauth/v2/accessToken", {
        "grant_type": "authorization_code", "code": q["code"], "client_id": cid, "client_secret": csec,
        "redirect_uri": "https://campusplug.club/"})
    os.environ.update(LINKEDIN_CLIENT_ID=cid, LINKEDIN_CLIENT_SECRET=csec,
                      LINKEDIN_ACCESS_TOKEN=tok["access_token"], LINKEDIN_REFRESH_TOKEN="")
    import pro_post
    print(pro_post.linkedin_check())
    set_secret("LINKEDIN_CLIENT_ID", cid)
    set_secret("LINKEDIN_CLIENT_SECRET", csec)
    set_secret("LINKEDIN_ACCESS_TOKEN", tok["access_token"])
    if tok.get("refresh_token"):
        set_secret("LINKEDIN_REFRESH_TOKEN", tok["refresh_token"])
        print("Saved (with refresh token - renews itself).")
    else:
        print("Saved. No refresh token was issued: rerun this every ~60 days.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    which = " ".join(sys.argv[1:]).strip().lower() or input("Which? youtube, tiktok, x or linkedin: ").strip().lower()
    fn = {"youtube": youtube, "tiktok": tiktok, "x": x, "linkedin": linkedin}.get(which)
    if not fn:
        sys.exit(f"Didn't understand {which!r} - use youtube, tiktok, x or linkedin.")
    fn()
