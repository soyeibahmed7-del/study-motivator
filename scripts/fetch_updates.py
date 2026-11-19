#!/usr/bin/env python3
"""
Collects HSC 2028 notices (official sites) and news (Google News) into updates.json.
Runs on GitHub Actions every day. Uses only Python's standard library (no pip install).
Each source is tried separately, so one broken website never stops the others.
"""
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "updates.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---- Official websites. EDIT THIS LIST to add or remove pages. ----
# Several pages per site are tried (home page + notice page). If a page does not
# exist or the site blocks GitHub, it is just reported as an error in the Info Center.
OFFICIAL = [
    {"id": "nctb", "name": "NCTB (জাতীয় শিক্ষাক্রম ও পাঠ্যপুস্তক বোর্ড)",
     "urls": ["https://nctb.gov.bd/", "https://nctb.gov.bd/site/view/notices"]},
    {"id": "boards", "name": "Inter-Board (শিক্ষা বোর্ড)",
     "urls": ["https://educationboard.gov.bd/", "https://educationboard.gov.bd/site/view/notices"]},
    {"id": "dhaka", "name": "ঢাকা শিক্ষা বোর্ড",
     "urls": ["https://dhakaeducationboard.gov.bd/", "https://dhakaeducationboard.gov.bd/site/view/notices"]},
    {"id": "moedu", "name": "শিক্ষা মন্ত্রণালয়",
     "urls": ["https://moedu.gov.bd/", "https://moedu.gov.bd/site/view/notices"]},
    {"id": "dshe", "name": "মাধ্যমিক ও উচ্চ শিক্ষা অধিদপ্তর",
     "urls": ["https://dshe.gov.bd/", "https://dshe.gov.bd/site/view/notices"]},
]
# ---- News searches (Google News RSS) ----
NEWS_QUERIES = ["HSC 2028 when:90d", "এইচএসসি ২০২৮ when:90d"]

# A link is "strong" if its text mentions HSC / 2028 / higher secondary ...
STRONG = ["hsc", "এইচএসসি", "2028", "higher secondary", "উচ্চ মাধ্যমিক", "উচ্চমাধ্যমিক",
          "ইন্টারমিডিয়েট", "intermediate", "একাদশ", "দ্বাদশ", "class 11", "class xi"]
# ... and "related" if it mentions syllabus / routine / notice etc.
WEAK = ["syllabus", "সিলেবাস", "routine", "রুটিন", "curriculum", "শিক্ষাক্রম", "সময়সূচি",
        "schedule", "notice", "নোটিশ", "প্রজ্ঞাপন", "বিজ্ঞপ্তি", "পরীক্ষা", "exam"]
MAX_OFFICIAL, MAX_NEWS = 10, 12

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def norm(text):
    """Lower-case and turn Bangla digits into 0-9 so '২০২৮' matches '2028'."""
    return text.translate(BN_DIGITS).lower()


def is_ssl_error(e):
    reason = getattr(e, "reason", None)
    return isinstance(e, ssl.SSLError) or isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(e).upper()


def fetch(url, timeout=20):
    """Download a page using normal TLS certificate verification."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "bn,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        raw = r.read(3_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")

# If a site blocks GitHub's servers we try to read it through these helpers instead.
# NOTE: results from them can be older (archive copy) - the site name in the Info Center says so.
PROXIES = [
    {"id": "jina", "label": "r.jina.ai দিয়ে আনা", "kind": "md",
     "build": lambda u: "https://r.jina.ai/" + u},
    {"id": "wayback", "label": "web.archive.org আর্কাইভ কপি, পুরনো হতে পারে", "kind": "html",
     "build": lambda u: "https://web.archive.org/web/2/" + u},
]
MD_LINK = re.compile(r"(?<!!)\[([^\]\n]{8,200})\]\((https?://[^)\s]+)")
WAYBACK_PREFIX = re.compile(r"^https?://web\.archive\.org/web/\d+[a-z_]*/", re.I)


def extract_md_links(text):
    return [{"title": re.sub(r"\s+", " ", m.group(1)).strip(), "url": m.group(2)} for m in MD_LINK.finditer(text)]


class LinkParser(HTMLParser):
    """Collects (text, href) for every <a> link on a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._href, self._title, self._buf = [], None, "", []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            a = dict(attrs)
            self._href, self._title, self._buf = a.get("href"), a.get("title") or "", []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip() or self._title.strip()
            self.links.append((text, self._href))
            self._href = None


def extract_links(html, base_url):
    p = LinkParser()
    p.feed(html)
    out = []
    for text, href in p.links:
        href = (href or "").strip()
        if len(text) < 8 or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        out.append({"title": text[:200], "url": urllib.parse.urljoin(base_url, href)})
    return out


def pick_relevant(links, limit):
    """Strong matches first, then related ones; no duplicates."""
    seen, strong, weak = set(), [], []
    for l in links:
        if l["url"] in seen:
            continue
        seen.add(l["url"])
        t = norm(l["title"])
        if any(k in t for k in STRONG):
            strong.append(dict(l, match="strong"))
        elif any(k in t for k in WEAK):
            weak.append(dict(l, match="related"))
    return (strong + weak)[:limit]


def links_via_proxy(px, url):
    """Read one page through a helper service and return its links."""
    text = fetch(px["build"](url), timeout=40)
    if px["kind"] == "md":
        return extract_md_links(text)
    out = []
    for l in extract_links(text, px["build"](url)):
        u = WAYBACK_PREFIX.sub("", l["url"])
        if u.startswith("http") and "web.archive.org" not in u:
            out.append({"title": l["title"], "url": u})
    return out


def collect_official(src):
    items, errors, ok, via = [], [], False, None
    for url in src["urls"]:
        try:
            html = fetch(url)
            ok = True
            items += extract_links(html, url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {type(e).__name__}: {e}")
    if not ok:                                   # site blocks us -> try the helpers
        for px in PROXIES:
            for url in src["urls"]:
                try:
                    links = links_via_proxy(px, url)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{px['id']}({url}): {type(e).__name__}: {e}")
                    continue
                if links:
                    items += links
                    ok, via = True, px
            if ok:
                break
    result = {"id": src["id"], "name": src["name"], "kind": "official", "url": src["urls"][0]}
    if via:
        result["name"] += " — " + via["label"]
    if not ok:
        result.update(status="error", error=" | ".join(errors)[:300], items=[])
    else:
        result.update(status="ok", items=pick_relevant(items, MAX_OFFICIAL))
        if errors and not via:
            result["note"] = "কিছু পেজ খোলা যায়নি"
    return result


def parse_rss(xml_text):
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        date = ""
        try:
            date = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            pass
        items.append({"title": title[:220], "url": link, "date": date,
                      "source": (it.findtext("source") or "").strip()})
    return items


def collect_news():
    items, errors, ok = [], [], False
    for q in NEWS_QUERIES:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": q, "hl": "bn", "gl": "BD", "ceid": "BD:bn"})
        try:
            items += parse_rss(fetch(url))
            ok = True
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")
    result = {"id": "news", "name": "সংবাদ (Google News)", "kind": "news",
              "url": "https://news.google.com/search?q=HSC%202028"}
    if not ok:
        result.update(status="error", error=" | ".join(errors)[:300], items=[])
        return result
    seen, uniq = set(), []
    for i in sorted(items, key=lambda x: x["date"], reverse=True):
        if i["title"] not in seen:
            seen.add(i["title"])
            uniq.append(i)
    result.update(status="ok", items=uniq[:MAX_NEWS])
    return result



# ---- User-managed and AI-discovered sources ----
CUSTOM_FILE = Path(__file__).resolve().parent.parent / "sources.json"
DISCOVERED_FILE = Path(__file__).resolve().parent.parent / "discovered_sources.json"


def valid_source_url(url):
    try:
        p = urllib.parse.urlparse(url)
        return p.scheme == "https" and bool(p.netloc) and not p.username and not p.password
    except Exception:
        return False


def read_source_list(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def collect_extra_source(src):
    name = str(src.get("name") or "অতিরিক্ত উৎস")[:120]
    url = str(src.get("url") or "").strip()
    source_type = src.get("type", "website")
    result = {"id": str(src.get("id") or re.sub(r"[^a-z0-9]+", "-", name.lower())[:40]),
              "name": name, "kind": src.get("kind", "custom"), "url": url}
    if not valid_source_url(url):
        result.update(status="error", error="শুধু বৈধ HTTPS URL গ্রহণ করা হয়", items=[])
        return result
    try:
        body = fetch(url)
        if source_type == "rss":
            items = parse_rss(body)
            result.update(status="ok", items=items[:MAX_NEWS])
        else:
            links = extract_links(body, url)
            picked = pick_relevant(links, MAX_OFFICIAL)
            result.update(status="ok", items=picked)
            if not picked:
                result["note"] = "এই পেজে HSC-সম্পর্কিত লিংক পাওয়া যায়নি"
    except Exception as e:  # noqa: BLE001
        result.update(status="error", error=f"{type(e).__name__}: {e}"[:300], items=[])
    return result


def discover_sources():
    """Use Gemini web grounding to discover candidate Bangladesh education sources.
    The key must be stored as a GitHub Actions secret, never in the browser."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("source discovery skipped: GEMINI_API_KEY secret is not configured")
        return read_source_list(DISCOVERED_FILE)

    prompt = (
        "Find up to 8 useful public HTTPS websites or RSS feeds for Bangladesh HSC 2028 "
        "students: NCTB, education boards, education ministry, DSHE, admission/exam notices "
        "and reputable education news. Prefer official sources. Return concise names and "
        "explain the source type. Do not include social-media profile URLs or login-only pages."
    )
    endpoint = ("https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-flash:generateContent")
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "tools": [{"google_search": {}}],
               "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200}}
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        chunks = (((data.get("candidates") or [{}])[0].get("groundingMetadata") or {})
                  .get("groundingChunks") or [])
        found, seen = [], set()
        for chunk in chunks:
            web = chunk.get("web") or {}
            url = str(web.get("uri") or "").strip()
            title = str(web.get("title") or "AI-আবিষ্কৃত শিক্ষা-তথ্য উৎস").strip()
            if not valid_source_url(url) or url in seen:
                continue
            host = urllib.parse.urlparse(url).hostname or ""
            if host.endswith("google.com") or host.endswith("googleusercontent.com"):
                continue
            seen.add(url)
            found.append({"id": "ai-" + str(len(found) + 1), "name": title[:120],
                          "url": url, "type": "website", "kind": "discovered"})
            if len(found) >= 8:
                break
        if found:
            DISCOVERED_FILE.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"AI source discovery: {len(found)} candidate sources saved")
            return found
        print("AI source discovery returned no usable HTTPS sources")
    except Exception as e:  # noqa: BLE001
        print(f"AI source discovery failed: {type(e).__name__}: {e}")
    return read_source_list(DISCOVERED_FILE)


def main():
    discovered = discover_sources()
    extras = read_source_list(CUSTOM_FILE) + discovered
    sources = [collect_official(s) for s in OFFICIAL] + [collect_news()]
    seen_urls = {s["url"] for s in OFFICIAL}
    for src in extras:
        url = str(src.get("url") or "").strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append(collect_extra_source(src))
    data = {"generated_at": datetime.now(timezone.utc).isoformat(), "sources": sources}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in sources:
        print(f"{s['id']:8} {s['status']:6} items={len(s['items'])} {s.get('error', '')[:100]}")


if __name__ == "__main__":
    main()
