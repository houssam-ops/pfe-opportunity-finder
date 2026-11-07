"""Manual imports and explicitly permitted public sources. Never fetch LinkedIn."""
import csv
import io
import json
import re
import time
import hashlib
import ipaddress
import socket
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import feedparser
import requests
from bs4 import BeautifulSoup
from models import Opportunity, valid_url, normalized

USER_AGENT = "PFE-Opportunity-Finder/1.0 (personal job research; public APIs)"
URL_RE = re.compile(r"https?://[^\s<>\"']+")
MAX_BYTES = 6 * 1024 * 1024

def clean_text(value):
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for element in soup(["script", "style", "noscript"]): element.decompose()
    return re.sub(r"[ \t]+", " ", soup.get_text("\n", strip=True)).strip()

def urls_in(text):
    return [u.rstrip(".,;!?)\u3002") for u in URL_RE.findall(text) if valid_url(u.rstrip(".,;!?)\u3002"))]

def is_linkedin(url):
    host = (urlsplit(url).hostname or "").lower()
    return host == "linkedin.com" or host.endswith(".linkedin.com") or host == "lnkd.in"

def record_from_mapping(row, source="File import"):
    row = {str(k).strip().lower(): str(v or "").strip() for k, v in row.items() if k}
    text = row.get("text", row.get("description", row.get("content", "")))
    raw_url = row.get("url", row.get("original_url", row.get("link", "")))
    warnings = []
    if raw_url and not valid_url(raw_url): warnings.append("Invalid original URL: " + raw_url[:180])
    app_url = row.get("application_url", row.get("apply_url", ""))
    if app_url and not valid_url(app_url):
        warnings.append("Invalid application URL: " + app_url[:180]); app_url = ""
    platform = row.get("source", source)
    if valid_url(raw_url) and is_linkedin(raw_url): platform = "LinkedIn (manual import)"
    return Opportunity(text=clean_text(text), url=raw_url if valid_url(raw_url) else "",
        application_url=app_url, company=row.get("company", ""), title=row.get("title", row.get("role", "")),
        location=row.get("location", ""), published_at=row.get("published_at", row.get("date", "")),
        source=platform, metadata={"import_warnings": warnings, "raw_url": raw_url})

def parse_text(text, source="Clipboard / text import"):
    """Use --- between posts; a URL-only list becomes pending records, never fabricated jobs."""
    text = text.strip()
    if not text: return []
    blocks = re.split(r"(?m)^\s*---+\s*$", text)
    records = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and all(valid_url(line) for line in lines):
            records.extend(record_from_mapping({"url": line}, source) for line in lines)
            continue
        # Blank-line separated posts are safe to split only when each starts with its own URL.
        pieces = re.split(r"\n\s*\n(?=https?://)", block.strip())
        for piece in pieces:
            urls = urls_in(piece)
            url = next((u for u in urls if is_linkedin(u)), urls[0] if urls else "")
            content = piece.strip()
            if url and content == url: content = ""
            row = record_from_mapping({"text": content, "url": url}, source)
            if row.text or row.url: records.append(row)
    return records

def parse_email(data, filename="alert.eml"):
    msg = BytesParser(policy=policy.default).parsebytes(data)
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is None: return []
    content = body.get_content()
    subject = str(msg.get("subject", "Job alert"))
    sender = str(msg.get("from", ""))
    text = clean_text(content)
    records = []
    if body.get_content_type() == "text/html":
        soup = BeautifulSoup(content, "html.parser")
        seen = set()
        for a in soup.find_all("a", href=True):
            url, label = a["href"], a.get_text(" ", strip=True)
            if not valid_url(url) or url in seen: continue
            if re.search(r"unsubscribe|preferences|privacy|desabonn|view.*browser|manage.*alert", label + " " + url, re.I): continue
            # Preserve each card separately; never assign the whole digest to every job.
            card = a.find_parent(["article", "li", "td"])
            context = clean_text(str(card)) if card else label
            if len(label) < 5 and len(context) < 25: continue
            seen.add(url)
            records.append(Opportunity(text=context, title=label if len(label) > 12 else "", url=url,
                source="Email alert (manual import)", metadata={"email_subject": subject,
                "email_sender": sender, "import_file": filename, "email_received": str(msg.get("date", "")),
                "import_warnings": ["Alert text may be partial; verify the original listing."]}))
    else:
        # Split paragraphs with their own job links, leave ambiguous prose as a single unconfirmed import.
        for block in re.split(r"\n\s*\n", text):
            if urls_in(block): records.extend(parse_text(block, "Email alert (manual import)"))
    if not records and text:
        records = [Opportunity(text=text, source="Email alert (manual import)", metadata={"email_subject": subject})]
    return records

def parse_import(data, filename):
    if len(data) > 10 * 1024 * 1024: raise ValueError("Import exceeds 10 MB.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".eml": return parse_email(data, filename)
    try: text = data.decode("utf-8-sig")
    except UnicodeDecodeError: text = data.decode("cp1252")
    if suffix == ".csv":
        try: dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error: dialect = csv.excel
        rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
        if rows and not {str(k).strip().lower() for k in rows[0] if k}.intersection({"text", "description", "content", "url", "original_url", "link"}):
            raise ValueError("CSV needs a text, description, content, url, original_url or link column.")
        return [record_from_mapping(r) for r in rows if any(r.values())]
    if suffix in (".txt", ".md"): return parse_text(text)
    if suffix in (".html", ".htm"):
        soup = BeautifulSoup(text, "html.parser")
        for a in soup.find_all("a", href=True):
            if valid_url(a["href"]): a.append(" " + a["href"])
        return parse_text(clean_text(str(soup)), "Saved HTML (manual import)")
    raise ValueError("Supported imports: TXT, CSV, EML, HTML.")

def assert_public_url(url):
    if not valid_url(url) or is_linkedin(url):
        raise ValueError("Only public HTTP(S) sources are supported; LinkedIn URLs require manual text import.")
    host = urlsplit(url).hostname
    for item in socket.getaddrinfo(host, None):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError("Private/local network sources are not permitted.")

class PublicClient:
    def __init__(self, settings): self.settings = settings

    def get(self, url, ttl=21600):
        """Bounded response, no auth, redirects validated individually, durable rate-limit cache."""
        cache = self.settings.root / "data/cache"
        cache.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()
        body, stamp = cache / (key + ".body"), cache / (key + ".stamp")
        if stamp.exists() and time.time() - stamp.stat().st_mtime < ttl:
            if body.exists() and body.stat().st_mtime >= stamp.stat().st_mtime: return body.read_bytes()
            raise RuntimeError("This source recently failed. Its cache cooldown prevents repeated requests.")
        # Mark the attempt, including failures (avoid rapid retries on provider errors).
        stamp.touch()
        with requests.Session() as client:
            client.trust_env = False
            for _ in range(5):
                assert_public_url(url)
                with client.get(url, headers={"User-Agent": USER_AGENT}, timeout=self.settings.request_timeout,
                                stream=True, allow_redirects=False) as response:
                    if response.status_code in (301,302,303,307,308):
                        url = urljoin(url, response.headers.get("Location", "")); continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > MAX_BYTES: raise ValueError("Source response exceeds 6 MB.")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    temp = body.with_suffix(".tmp")
                    temp.write_bytes(content); temp.replace(body)
                    return content
        raise ValueError("Too many redirects.")

    def json(self, url, ttl=21600): return json.loads(self.get(url, ttl))

def collect_source(source, settings):
    """Yield normalized records; caller enforces a hard process timeout per source."""
    client = PublicClient(settings)
    kind, name = source["type"], source["name"]
    maximum = settings.max_items
    if kind == "remotive":
        data = client.json("https://remotive.com/api/remote-jobs")
        jobs = data.get("jobs", [])
        # Prioritize internships/juniors before applying the configured bound.
        jobs.sort(key=lambda j: not bool(re.search(r"intern|junior|graduate|trainee|stage|pfe", j.get("title", ""), re.I)))
        for j in jobs[:maximum]:
            yield Opportunity(text=clean_text(j.get("description", "")), url=j.get("url", ""),
                title=j.get("title", ""), company=j.get("company_name", ""),
                location="Remote — " + j.get("candidate_required_location", "Unspecified eligibility"),
                published_at=j.get("publication_date", ""), source="Remotive", trusted_listing=True,
                metadata={"job_type": j.get("job_type", ""), "note": "Remotive public API listings are delayed by 24 hours."})
    elif kind == "smartrecruiters":
        company = source["company"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", company): raise ValueError("Invalid company identifier")
        base = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
        country = source.get("country", "ma")
        if not re.fullmatch(r"[a-zA-Z]{2}", country): raise ValueError("Country must be a two-letter code")
        offset = 0
        while offset < maximum:
            data = client.json(f"{base}?country={country}&limit={min(100, maximum-offset)}&offset={offset}")
            jobs = data.get("content", [])
            if not jobs: break
            for j in jobs:
                title = j.get("name", "")
                if not re.search(r"intern|stage|stagiaire|pfe|junior|graduate|cyber|devops|cloud|secur|software|develop|soc", title, re.I): continue
                identifier = str(j.get("id", ""))
                if not re.fullmatch(r"[a-zA-Z0-9-]+", identifier): continue
                detail = client.json(f"{base}/{identifier}")
                sections = detail.get("jobAd", {}).get("sections", {})
                description = "\n".join(clean_text(v.get("text", "")) for v in sections.values() if isinstance(v, dict))
                loc = detail.get("location", j.get("location", {}))
                url = detail.get("postingUrl", "")
                yield Opportunity(text=description, url=url,
                    application_url=detail.get("applyUrl", ""), title=title,
                    company=detail.get("company", {}).get("name", company),
                    location=", ".join(str(loc[k]) for k in ("city", "country") if loc.get(k)),
                    source=name, published_at=detail.get("releasedDate", j.get("releasedDate", "")), trusted_listing=True,
                    metadata={"job_type": detail.get("typeOfEmployment", {}).get("label", "")})
            offset += len(jobs)
            if offset >= data.get("totalFound", offset): break
    elif kind == "lever":
        company = source["company"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", company): raise ValueError("Invalid company identifier")
        host = "api.eu.lever.co" if source.get("region") == "eu" else "api.lever.co"
        jobs = client.json(f"https://{host}/v0/postings/{company}?mode=json")
        for j in jobs[:maximum]:
            text = j.get("descriptionPlain", "") + "\n" + "\n".join(clean_text(x.get("content", "")) for x in j.get("lists", []))
            yield Opportunity(text=text, url=j.get("hostedUrl", ""), application_url=j.get("applyUrl", ""),
                title=j.get("text", ""), company=company, location=j.get("categories", {}).get("location", ""),
                published_at=datetime.fromtimestamp(j["createdAt"]/1000, timezone.utc).isoformat() if j.get("createdAt") else "",
                source=name, trusted_listing=True, metadata={"job_type": j.get("categories", {}).get("commitment", "")})
    elif kind in ("rss", "jsonld"):
        if not source.get("permission_confirmed") or not valid_url(source.get("permission_url", "")):
            raise ValueError("Enable custom extraction only after recording permission and its terms URL.")
        url = source["url"]
        if kind == "rss":
            feed = feedparser.parse(client.get(url))
            if feed.bozo and not feed.entries: raise ValueError("This source is not a readable RSS/Atom feed")
            for e in feed.entries[:maximum]:
                yield Opportunity(text=clean_text(e.get("summary", "")), title=e.get("title", ""),
                    url=e.get("link", ""), source=name, published_at=e.get("published", ""),
                    trusted_listing=bool(source.get("job_feed", False)))
        else:
            robots_url = urljoin(url, "/robots.txt")
            robots = RobotFileParser()
            robots.parse(client.get(robots_url).decode("utf-8", errors="replace").splitlines())
            if not robots.can_fetch(USER_AGENT, url): raise ValueError("robots.txt disallows this page")
            soup = BeautifulSoup(client.get(url), "html.parser")
            def walk(value):
                if isinstance(value, list):
                    for x in value: yield from walk(x)
                if isinstance(value, dict):
                    types = value.get("@type", [])
                    if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types: yield value
                    for v in value.values():
                        if isinstance(v, (dict, list)): yield from walk(v)
            count = 0
            for script in soup.find_all("script", type="application/ld+json"):
                try: entries = list(walk(json.loads(script.string or script.get_text())))
                except (ValueError, TypeError): continue
                for j in entries:
                    if count >= maximum: return
                    loc = j.get("jobLocation", {})
                    if isinstance(loc, list): loc = loc[0] if loc else {}
                    addr = loc.get("address", {}) if isinstance(loc, dict) else {}
                    yield Opportunity(text=clean_text(j.get("description", "")), title=j.get("title", ""),
                        company=j.get("hiringOrganization", {}).get("name", ""), url=urljoin(url, j.get("url", url)),
                        location=", ".join(str(addr[k]) for k in ("addressLocality", "addressCountry") if addr.get(k)),
                        published_at=j.get("datePosted", ""), source=name, trusted_listing=True,
                        metadata={"job_type": j.get("employmentType", ""), "valid_through": j.get("validThrough", "")})
                    count += 1
    elif kind == "prospects":
        for p in source.get("companies", [])[:maximum]:
            yield Opportunity(company=p["company"], title="Spontaneous application research",
                url=p["url"], source="Company careers directory", kind="prospect", location=p.get("location", "Morocco"),
                text=p.get("note", ""), metadata={"verified_on": p.get("verified_on", ""),
                "note": "Research lead only. No current internship vacancy is claimed."})
    else: raise ValueError(f"Unsupported source type: {kind}")

def load_sources(path):
    sources = json.loads(Path(path).read_text())
    if not isinstance(sources, list): raise ValueError("sources.json must contain a list")
    for source in sources:
        if not isinstance(source, dict) or not all(k in source for k in ("type", "name")):
            raise ValueError("Each source needs type and name")
        if not isinstance(source["name"],str) or not source["name"].strip():
            raise ValueError("Source names must be nonempty text")
        if source["type"] not in ("remotive","smartrecruiters","lever","rss","jsonld","prospects"):
            raise ValueError("Unsupported source type: " + str(source["type"]))
        for flag in ("enabled","permission_confirmed","job_feed"):
            if flag in source and not isinstance(source[flag],bool):
                raise ValueError(f"{source['name']}: {flag} must be true or false")
        if not source.get("enabled",False): continue
        if source["type"] in ("lever","smartrecruiters"):
            if not isinstance(source.get("company"),str) or not re.fullmatch(r"[A-Za-z0-9_-]+",source["company"]):
                raise ValueError(f"{source['name']}: a valid company identifier is required")
        if source["type"] == "smartrecruiters":
            country=source.get("country","ma")
            if not isinstance(country,str) or not re.fullmatch(r"[a-zA-Z]{2}",country):
                raise ValueError(f"{source['name']}: country must be a two-letter code")
        if source["type"] in ("rss","jsonld"):
            if not valid_url(source.get("url","")) or is_linkedin(source["url"]):
                raise ValueError(f"{source['name']}: a public source URL is required")
            if source.get("permission_confirmed") is not True or not valid_url(source.get("permission_url","")):
                raise ValueError(f"{source['name']}: documented permission is required")
        if source["type"] == "prospects":
            companies=source.get("companies")
            if not isinstance(companies,list) or any(not isinstance(p,dict) or not isinstance(p.get("company"),str) or not p["company"].strip() or not valid_url(p.get("url","")) for p in companies):
                raise ValueError(f"{source['name']}: companies must contain names and valid URLs")
    return sources
