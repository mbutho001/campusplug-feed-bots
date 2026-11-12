"""X and LinkedIn posters for the opportunity slots (called from channel_post.py).

X        native X API v2, OAuth 1.0a user context (the @CampusPlug_SA app's own keys):
         secrets X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET.
         One square flier + the slot caption (which already carries the tracked Play link).
LinkedIn Composio (its LinkedIn app already has w_organization_social, so the Campus Plug
         company page can post without our own Community Management API review):
         secrets COMPOSIO_API_KEY, LINKEDIN_CONNECTED_ACCOUNT (Composio connected account id of
         the page admin), optional LINKEDIN_ORG_ID (default 145421420 = linkedin.com/company/campusplugsa).

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


# ---------------------------------------------------------------- LinkedIn (Composio)

def _composio():
    key, ca = _env("COMPOSIO_API_KEY"), _env("LINKEDIN_CONNECTED_ACCOUNT")
    if not (key and ca):
        return None, None
    from composio import Composio  # pip install composio
    return Composio(api_key=key), ca


def _li_exec(client, ca, slug, args):
    res = client.tools.execute(slug, args, connected_account_id=ca, dangerously_skip_version_check=True)
    res = res if isinstance(res, dict) else getattr(res, "model_dump", lambda: res)()
    if not res.get("successful"):
        raise ProPostError(f"LinkedIn {slug}: {res.get('error') or res}"[:500])
    return res.get("data") or {}


def linkedin_post(text, images):
    client, ca = _composio()
    if not client:
        return None, "COMPOSIO_API_KEY / LINKEDIN_CONNECTED_ACCOUNT not set - LinkedIn posting is off"
    org = _env("LINKEDIN_ORG_ID") or LINKEDIN_ORG_DEFAULT
    args = {"author": f"urn:li:organization:{org}", "commentary": text[:3000], "visibility": "PUBLIC"}
    if images:
        args["images"] = [str(p) for p in images[:20]]   # the SDK uploads local paths for file fields
    data = _li_exec(client, ca, "LINKEDIN_CREATE_LINKED_IN_POST", args)
    pid = data.get("x_restli_id") or data.get("id") or data.get("post_id") or json.dumps(data)[:120]
    return pid, f"to linkedin.com/company/{org}"


def linkedin_check():
    client, ca = _composio()
    if not client:
        return "LinkedIn: secrets not set"
    data = _li_exec(client, ca, "LINKEDIN_GET_COMPANY_INFO", {"role": "ADMINISTRATOR"})
    return "LinkedIn: admin of " + json.dumps(data)[:200]
