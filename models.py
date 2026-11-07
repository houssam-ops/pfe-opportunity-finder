"""Shared records and conservative URL/content identity."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import hashlib
import re
import unicodedata

def normalized(text):
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", text).strip()

def valid_url(value):
    value = str(value or "").strip()
    try:
        p = urlsplit(value)
        return bool(p.scheme in ("https", "http") and p.hostname and "." in p.hostname
                    and not p.username and not p.password and not re.search(r"[\s<>\x00-\x1f]", value)
                    and (p.port is None or 1 <= p.port <= 65535))
    except ValueError: return False

def canonical_url(value):
    if not valid_url(value): return ""
    p = urlsplit(value.strip())
    host = p.netloc.lower()
    if host in ("linkedin.com", "www.linkedin.com", "m.linkedin.com"): host = "www.linkedin.com"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("trk", "trackingid", "ref", "fbclid", "gclid")]
    activity = re.search(r"(?:activity[:\-]|ugcpost[:\-])(\d{10,})", value, re.I)
    if host == "www.linkedin.com" and activity:
        return f"https://www.linkedin.com/feed/update/urn:li:activity:{activity[1]}/"
    return urlunsplit((p.scheme.lower(), host, p.path.rstrip("/") or "/", urlencode(sorted(query)), ""))

def now_iso(): return datetime.now(timezone.utc).isoformat()

@dataclass
class Opportunity:
    text: str = ""
    url: str = ""
    application_url: str = ""
    company: str = ""
    title: str = ""
    location: str = ""
    source: str = "Manual import"
    published_at: str = ""
    kind: str = "vacancy"
    trusted_listing: bool = False
    metadata: dict = field(default_factory=dict)
    provenance: list = field(default_factory=list)

    @property
    def key(self):
        # Text-only items remain stable; an added URL can be merged by the database.
        identity = canonical_url(self.url) or ("text:" + normalized(self.text))
        return hashlib.sha256(identity.encode()).hexdigest()

    @property
    def text_key(self):
        content = normalized(self.text)
        return hashlib.sha256(content.encode()).hexdigest() if len(content) >= 70 else ""

    def to_dict(self): return asdict(self)

def from_dict(value):
    return Opportunity(**{k:v for k,v in value.items() if k in Opportunity.__dataclass_fields__})
