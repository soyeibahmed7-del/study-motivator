#!/usr/bin/env python3
"""
Collects HSC 2028 notices (official sites) and news (Google News) into updates.json.
Runs on GitHub Actions every day. Uses only Python's standard library (no pip install).
Each source is tried separately, so one broken website never stops the others.
"""
import json
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
    """Download a page. Many .gov.bd sites have bad certificates, so SSL problems are retried without SSL checking.
    Timeouts / 'connection refused' are NOT retried (that usually means the site blocks GitHub's servers)."""
    last = None
    for verify in (True, False):
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "bn,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read(3_000_000)
                charset = r.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if not is_ssl_error(e):
                break
    raise last


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


def main():
    sources = [collect_official(s) for s in OFFICIAL] + [collect_news()]
    data = {"generated_at": datetime.now(timezone.utc).isoformat(), "sources": sources}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in sources:
        print(f"{s['id']:8} {s['status']:6} items={len(s['items'])} {s.get('error', '')[:100]}")


if __name__ == "__main__":
    main()
