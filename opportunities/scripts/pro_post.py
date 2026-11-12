"""X and LinkedIn posters for the opportunity slots (called from channel_post.py).

X        native X API v2, OAuth 1.0a user context (the @CampusPlug_SA app's own keys):
         secrets X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET.
         One square flier + the slot caption (which already carries the tracked Play link).
LinkedIn our own app "Campus Plug Publisher" (264231871) with the Community Management API, posting
         as urn:li:organization:145421420: secrets LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET and
         LINKEDIN_REFRESH_TOKEN (rotates into state/linkedin-token.json) or LINKEDIN_ACCESS_TOKEN (60 days).

A platform without its secrets is off: the poster returns (None, reason).
"""
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
import urllib.parse
import urllib.request
import uuid

X_API = "https://api.x.com/2"
LINKEDIN_ORG_DEFAULT = "145421420"


class ProPostError(RuntimeError):
    pass


def _env(name):
    return os.environ.get(name, "").strip()


# ---------------------------------------------------------------- X (OAuth 1.0a)

def _pct(s):
    return urllib.parse.quote(str(s), safe="~")


def _oauth_header(method, url, keys):
    ck, cs, tok, ts = keys
    params = {"oauth_consumer_key": ck, "oauth_nonce": secrets.token_hex(16),
              "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
              "oauth_token": tok, "oauth_version": "1.0"}
    base_url, _, query = url.partition("?")
    allp = dict(params)
    allp.update(dict(urllib.parse.parse_qsl(query)))
    pstr = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(allp.items()))
    base = "&".join([method.upper(), _pct(base_url), _pct(pstr)])
    sig = base64.b64encode(hmac.new(f"{_pct(cs)}&{_pct(ts)}".encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(params.items()))


def _x_request(method, url, keys, body=None, content_type=None, timeout=120):
    headers = {"Authorization": _oauth_header(method, url, keys)}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise ProPostError(f"X {method} {url} -> HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")


def x_keys():
    keys = tuple(_env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))
    return keys if all(keys) else None


def x_upload(keys, path):
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="media_category"\r\n\r\ntweet_image\r\n'.encode(),
        (f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="{os.path.basename(str(path))}"\r\n'
         f"Content-Type: {mime}\r\n\r\n").encode() + open(path, "rb").read() + b"\r\n",
        f"--{boundary}--\r\n".encode()]
    r = _x_request("POST", f"{X_API}/media/upload", keys, b"".join(parts), f"multipart/form-data; boundary={boundary}")
    mid = (r.get("data") or {}).get("id") or r.get("media_id_string")
    if not mid:
        raise ProPostError(f"X media upload: no id in {r}")
    return mid


def x_post(text, images):
    keys = x_keys()
    if not keys:
        return None, "X_* secrets not set - X posting is off"
    media = [x_upload(keys, p) for p in images[:4]]
    body = {"text": text}
    if media:
        body["media"] = {"media_ids": media}
    r = _x_request("POST", f"{X_API}/tweets", keys, json.dumps(body).encode(), "application/json")
    tid = (r.get("data") or {}).get("id")
    if not tid:
        raise ProPostError(f"X post: {r}")
    return tid, f"https://x.com/CampusPlug_SA/status/{tid}"


def x_check():
    keys = x_keys()
    if not keys:
        return "X: secrets not set"
    r = _x_request("GET", f"{X_API}/users/me", keys)
    return f"X: @{(r.get('data') or {}).get('username')}"


# ---------------------------------------------------------------- LinkedIn (own app, posts as the page)
# App "Campus Plug Publisher" (264231871), verified by the Campus Plug page; needs the Community
# Management API (requested 2026-11-12). Composio's managed LinkedIn app has no organization scopes.
LI_API = "https://api.linkedin.com/rest"
LI_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state", "linkedin-token.json")


def _li_token():
    """Access token: refreshed from the newest refresh token when one exists, else the stored token."""
    cid, csec = _env("LINKEDIN_CLIENT_ID"), _env("LINKEDIN_CLIENT_SECRET")
    saved = {}
    if os.path.exists(LI_STATE):
        try:
            saved = json.load(open(LI_STATE))
        except ValueError:
            saved = {}
    refresh = saved.get("refresh_token") or _env("LINKEDIN_REFRESH_TOKEN")
    if refresh and cid and csec:
        data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh,
                                       "client_id": cid, "client_secret": csec}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    "https://www.linkedin.com/oauth/v2/accessToken", data=data), timeout=60) as r:
                tok = json.loads(r.read())
            os.makedirs(os.path.dirname(LI_STATE), exist_ok=True)
            json.dump({"refresh_token": tok.get("refresh_token", refresh), "refreshed": time.strftime("%Y-%m-%d")},
                      open(LI_STATE, "w"))
            os.chmod(LI_STATE, 0o600)
            return tok["access_token"]
        except urllib.error.HTTPError as e:
            raise ProPostError(f"LinkedIn token refresh: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    return _env("LINKEDIN_ACCESS_TOKEN") or None


def _li(method, path, tok, body=None, raw=None, ctype="application/json", url=None):
    headers = {"Authorization": f"Bearer {tok}", "LinkedIn-Version": _env("LINKEDIN_VERSION") or "202510",
               "X-Restli-Protocol-Version": "2.0.0"}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url or f"{LI_API}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            txt = r.read()
            return json.loads(txt) if txt else {}, dict(r.headers)
    except urllib.error.HTTPError as e:
        raise ProPostError(f"LinkedIn {method} {path or url} -> HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")


def linkedin_post(text, images):
    tok = _li_token()
    if not tok:
        return None, "LINKEDIN_* secrets not set - LinkedIn posting is off"
    org = f"urn:li:organization:{_env('LINKEDIN_ORG_ID') or LINKEDIN_ORG_DEFAULT}"
    ids = []
    for p in images[:20]:
        init, _ = _li("POST", "/images?action=initializeUpload", tok, {"initializeUploadRequest": {"owner": org}})
        v = init["value"]
        _li("PUT", "", tok, raw=open(p, "rb").read(), ctype="application/octet-stream", url=v["uploadUrl"])
        ids.append(v["image"])
    post = {"author": org, "commentary": text[:3000], "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    if len(ids) == 1:
        post["content"] = {"media": {"id": ids[0]}}
    elif ids:
        post["content"] = {"multiImage": {"images": [{"id": i} for i in ids]}}
    _, hdr = _li("POST", "/posts", tok, post)
    pid = hdr.get("x-restli-id") or hdr.get("X-RestLi-Id") or "posted"
    return pid, f"to linkedin.com/company/{org.rsplit(':', 1)[1]}"


def linkedin_check():
    tok = _li_token()
    if not tok:
        return "LinkedIn: secrets not set"
    org = _env("LINKEDIN_ORG_ID") or LINKEDIN_ORG_DEFAULT
    data, _ = _li("GET", f"/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED", tok)
    orgs = [e.get("organization") for e in data.get("elements", [])]
    return f"LinkedIn: admin of {orgs}; posting as urn:li:organization:{org}"
