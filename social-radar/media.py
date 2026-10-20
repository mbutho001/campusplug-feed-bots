"""Download an image, verify it really is an image, and shrink it hard.

Every picture in the digest is re-encoded to a small JPEG so a mail that lands
every three hours stays light on data.
"""
import io
from pathlib import Path

from PIL import Image

CACHE = Path(__file__).resolve().parent / "cache"


def grab(fetcher, url, cfg, log):
    """Return (jpeg_bytes, width, height) or None if it is not a usable image."""
    m = cfg["media"]
    resp = fetcher.get(url, retries=1, backoff=3, stream=True)
    if resp is None:
        return None
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype == "image/gif":
        limit = m["reject_gif_over_bytes"]      # animated GIFs are the heavy ones
    elif ctype in m["allowed_content_types"]:
        limit = m["max_download_bytes"]
    else:
        log(f"  media: skip {ctype or 'unknown type'} {url[:70]}")
        resp.close()
        return None

    buf = io.BytesIO()
    for chunk in resp.iter_content(65536):
        buf.write(chunk)
        if buf.tell() > limit:
            log(f"  media: skip oversize ({buf.tell()} > {limit}) {url[:70]}")
            resp.close()
            return None
    resp.close()

    try:
        img = Image.open(io.BytesIO(buf.getvalue()))
        img.seek(0)                              # first frame only, never a clip
        img = img.convert("RGB")
    except Exception as exc:                     # noqa: BLE001 - any decode failure is a skip
        log(f"  media: undecodable ({exc}) {url[:70]}")
        return None

    w, h = img.size
    if w < 200 or h < 150:
        return None
    if w > m["thumb_max_width"]:
        img = img.resize((m["thumb_max_width"], int(h * m["thumb_max_width"] / w)),
                         Image.LANCZOS)

    quality = m["thumb_quality"]
    for _ in range(4):
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True)
        if out.tell() <= m["max_thumb_bytes"] or quality <= 35:
            return out.getvalue(), img.size[0], img.size[1]
        quality -= 12
    return None
