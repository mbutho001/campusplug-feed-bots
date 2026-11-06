#!/usr/bin/env python3
"""Turn a short-lived Meta user token into the never-expiring secrets meta_post.py uses.

Run it yourself (the values are typed hidden and go straight into GitHub secrets;
nothing is printed or written to disk):

    python3 opportunities/scripts/meta_token.py facebook
        asks: App ID, App secret, short-lived USER token from the Graph API Explorer
              (permissions: pages_show_list, pages_manage_posts, pages_read_engagement,
               instagram_basic, instagram_content_publish, business_management)
        sets: META_PAGE_TOKEN  (Page token for the CampusPlug Page; does not expire)

    python3 opportunities/scripts/meta_token.py threads
        asks: Threads App ID, Threads App secret, short-lived Threads token
              (threads_basic, threads_content_publish)
        sets: THREADS_TOKEN    (60-day token; meta_post.py refreshes it weekly)

Needs the gh CLI logged in with access to mbutho001/campusplug-feed-bots.
"""
import getpass
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = "mbutho001/campusplug-feed-bots"
GRAPH = "https://graph.facebook.com/v23.0"
PAGE_NAME = "CampusPlug"


def get(url, params, fatal=True):
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        err = json.loads(exc.read() or b"{}").get("error", {})
        if not fatal:
            return None
        sys.exit(f"Meta said: {err.get('message', exc)}")


def set_secret(name, value):
    subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value.encode(), check=True)
    print(f"{name} saved to {REPO}")


def facebook():
    app_id = input("App ID: ").strip()
    secret = getpass.getpass("App secret (hidden): ").strip()
    short = getpass.getpass("Short-lived user token from Graph API Explorer (hidden): ").strip()
    long_user = get(f"{GRAPH}/oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": app_id,
        "client_secret": secret, "fb_exchange_token": short})["access_token"]
    pages = get(f"{GRAPH}/me/accounts", {"fields": "id,name,access_token,instagram_business_account{username}",
                                        "access_token": long_user}).get("data", [])
    page = next((p for p in pages if p.get("name") == PAGE_NAME), None)
    if not page:
        sys.exit("CampusPlug Page not in this token's pages: " + ", ".join(p.get("name", "?") for p in pages))
    ig = (page.get("instagram_business_account") or {}).get("username")
    print(f"Page: {page['name']} ({page['id']}), Instagram: @{ig}" if ig else
          f"Page: {page['name']} ({page['id']}) - WARNING: no Instagram linked to this token")
    set_secret("META_PAGE_TOKEN", page["access_token"])


def threads():
    app_id = input("Threads App ID: ").strip()
    secret = getpass.getpass("Threads App secret (hidden): ").strip()
    short = getpass.getpass("Threads token - short-lived, or long-lived from the User Token Generator (hidden): ").strip()
    swapped = get("https://graph.threads.net/access_token", {
        "grant_type": "th_exchange_token", "client_secret": secret, "access_token": short}, fatal=False)
    # The dashboard's User Token Generator already hands out a 60-day token; that one can't be exchanged.
    long = swapped["access_token"] if swapped else short
    me = get("https://graph.threads.net/v1.0/me", {"fields": "username", "access_token": long})
    print(f"Threads: @{me.get('username')} (app {app_id})")
    set_secret("THREADS_TOKEN", long)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which == "facebook":
        facebook()
    elif which == "threads":
        threads()
    else:
        sys.exit(__doc__)
