"""SEO / AEO / GEO audit engine.

Deterministic, rule-based checks (no API key needed) that also generate
copy-paste fixes: meta tags, FAQPage JSON-LD, Organization JSON-LD,
robots.txt snippet and a draft llms.txt.
"""
import asyncio
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; AEOAuditBot/1.0)"
AI_BOTS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "anthropic-ai",
    "PerplexityBot", "Google-Extended", "CCBot",
]
QUESTION_RE = re.compile(
    r"^(what|how|why|when|where|who|which|can|should|does|do|is|are)\b", re.I
)
STAT_RE = re.compile(
    r"(\d+(\.\d+)?\s?%|[$€£₹]\s?\d[\d,\.]*|\b(19|20)\d{2}\b|\b\d{1,3}(,\d{3})+\b)"
)
SOCIAL_HOSTS = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "github.com", "wikipedia.org", "wikidata.org", "crunchbase.com",
)


class AuditError(Exception):
    pass


# --------------------------------------------------------------------------
# URL handling / SSRF protection
# --------------------------------------------------------------------------
def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise AuditError("Please enter a URL.")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise AuditError("That does not look like a valid http(s) URL.")
    return raw


def assert_public_host(host: str) -> None:
    """Block requests to private/internal addresses (SSRF)."""
    if os.environ.get("ALLOW_PRIVATE") == "1":
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise AuditError(f"Could not resolve host: {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise AuditError("Private or internal addresses cannot be audited.")


async def _get(client: httpx.AsyncClient, url: str):
    try:
        return await client.get(url)
    except Exception:
        return None


# --------------------------------------------------------------------------
# robots.txt parsing
# --------------------------------------------------------------------------
def parse_robots(text: str) -> dict:
    """Return {user-agent(lower): [(directive, value), ...]}."""
    groups, agents, rules, last_was_agent = {}, [], [], False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = [s.strip() for s in line.split(":", 1)]
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent and agents:
                for a in agents:
                    groups.setdefault(a, []).extend(rules)
                agents, rules = [], []
            agents.append(val.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            rules.append((key, val))
            last_was_agent = False
    for a in agents:
        groups.setdefault(a, []).extend(rules)
    return groups


def bot_blocked(groups: dict, bot: str) -> bool:
    rules = groups.get(bot.lower())
    if rules is None:
        rules = groups.get("*", [])
    disallow_all = any(d == "disallow" and v == "/" for d, v in rules)
    allow_root = any(d == "allow" and v in ("/", "") for d, v in rules)
    return disallow_all and not allow_root


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def extract_jsonld(soup) -> list:
    out = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            out.append(node)
            if "@graph" in node:
                walk(node["@graph"])

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(s.string or s.get_text() or ""))
        except Exception:
            continue
    return out


def types_of(nodes) -> set:
    found = set()
    for n in nodes:
        t = n.get("@type")
        if isinstance(t, list):
            found.update(str(x) for x in t)
        elif t:
            found.add(str(t))
    return found


def check(pillar, cid, title, status, weight, detail, solution="", code="", lang=""):
    return {
        "pillar": pillar, "id": cid, "title": title, "status": status,
        "weight": weight, "detail": detail, "solution": solution,
        "code": code, "lang": lang,
    }


def score(checks, pillar) -> int:
    items = [c for c in checks if c["pillar"] == pillar]
    total = sum(c["weight"] for c in items) or 1
    got = sum(c["weight"] * {"pass": 1, "warn": 0.5, "fail": 0}[c["status"]] for c in items)
    return round(100 * got / total)


def first_sentence(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "…"


# --------------------------------------------------------------------------
# main audit
# --------------------------------------------------------------------------
async def audit(raw_url: str) -> dict:
    url = normalize_url(raw_url)
    p = urlparse(url)
    assert_public_host(p.hostname)
    base = f"{p.scheme}://{p.netloc}"

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=8.0, headers={"User-Agent": UA}
    ) as client:
        page, robots, llms, sitemap = await asyncio.gather(
            _get(client, url), _get(client, f"{base}/robots.txt"),
            _get(client, f"{base}/llms.txt"), _get(client, f"{base}/sitemap.xml"),
        )

    if page is None:
        raise AuditError("Could not fetch the page. Check the URL and try again.")
    if page.status_code >= 400:
        raise AuditError(f"The page returned HTTP {page.status_code}.")
    final_url = str(page.url)
    # Re-check the final host after redirects (SSRF via redirect).
    assert_public_host(urlparse(final_url).hostname)

    soup = BeautifulSoup(page.text, "html.parser")
    jsonld = extract_jsonld(soup)
    ld_types = types_of(jsonld)

    # Body text without scripts/styles
    body_soup = BeautifulSoup(page.text, "html.parser")
    for t in body_soup(["script", "style", "noscript", "nav", "footer", "header"]):
        t.decompose()
    body_text = body_soup.get_text(" ", strip=True)
    wc = words(body_text)

    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    md_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    meta_desc = (md_tag.get("content") or "").strip() if md_tag else ""
    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    headings = [(int(h.name[1]), h.get_text(" ", strip=True)) for h in soup.find_all(re.compile("^h[1-6]$"))]
    paragraphs = [x.get_text(" ", strip=True) for x in body_soup.find_all("p") if words(x.get_text()) >= 8]
    site_name = urlparse(final_url).hostname.replace("www.", "")
    brand = (title.split("|")[-1].split(" - ")[-1].strip() if title else site_name) or site_name
    topic = h1s[0] if h1s else (title.split("|")[0].strip() if title else site_name)

    checks = []
    add = checks.append

    # ------------------------------ SEO ---------------------------------
    if not title:
        add(check("seo", "title", "Title tag", "fail", 10, "No <title> found.",
                  "Add a unique title of 30–60 characters with your primary keyword first.",
                  f"<title>{first_sentence(topic, 45)} | {brand}</title>", "html"))
    elif not 30 <= len(title) <= 60:
        add(check("seo", "title", "Title tag length", "warn", 10,
                  f"Title is {len(title)} characters (ideal 30–60): “{title}”.",
                  "Rewrite the title to fit 30–60 characters so it is not truncated in results.",
                  f"<title>{first_sentence(topic, 45)} | {brand}</title>", "html"))
    else:
        add(check("seo", "title", "Title tag", "pass", 10, f"{len(title)} characters. Good."))

    if not meta_desc:
        suggestion = first_sentence(paragraphs[0], 155) if paragraphs else f"{topic} — {brand}."
        add(check("seo", "meta", "Meta description", "fail", 10, "No meta description found.",
                  "Add a 120–160 character description that summarises the page and invites the click.",
                  f'<meta name="description" content="{suggestion}">', "html"))
    elif not 120 <= len(meta_desc) <= 160:
        add(check("seo", "meta", "Meta description length", "warn", 10,
                  f"{len(meta_desc)} characters (ideal 120–160).",
                  "Adjust the description length so it displays fully in search results."))
    else:
        add(check("seo", "meta", "Meta description", "pass", 10, f"{len(meta_desc)} characters. Good."))

    canon = soup.find("link", rel="canonical")
    if canon and canon.get("href"):
        add(check("seo", "canonical", "Canonical URL", "pass", 6, f"Canonical: {canon['href']}"))
    else:
        add(check("seo", "canonical", "Canonical URL", "fail", 6, "No canonical link found.",
                  "Declare the preferred URL to avoid duplicate-content dilution.",
                  f'<link rel="canonical" href="{final_url}">', "html"))

    if len(h1s) == 1:
        add(check("seo", "h1", "Single H1", "pass", 8, f"H1: “{h1s[0]}”"))
    elif not h1s:
        add(check("seo", "h1", "Single H1", "fail", 8, "No <h1> found.",
                  "Add exactly one H1 that states the page topic.", f"<h1>{topic}</h1>", "html"))
    else:
        add(check("seo", "h1", "Single H1", "warn", 8, f"{len(h1s)} H1 tags found.",
                  "Keep one H1 and demote the others to H2."))

    skips = [(a, b) for (a, _), (b, _) in zip(headings, headings[1:]) if b - a > 1]
    add(check("seo", "hierarchy", "Heading hierarchy", "pass" if not skips else "warn", 4,
              "Heading levels are sequential." if not skips else f"{len(skips)} skipped level(s), e.g. H{skips[0][0]} → H{skips[0][1]}.",
              "" if not skips else "Do not skip heading levels; nest H3 under H2, and so on."))

    imgs = soup.find_all("img")
    no_alt = [i for i in imgs if not (i.get("alt") or "").strip()]
    if imgs and no_alt:
        add(check("seo", "alt", "Image alt text", "warn" if len(no_alt) < len(imgs) / 2 else "fail", 5,
                  f"{len(no_alt)} of {len(imgs)} images lack alt text.",
                  "Add short descriptive alt text to each meaningful image."))
    else:
        add(check("seo", "alt", "Image alt text", "pass", 5, "All images have alt text." if imgs else "No images found."))

    add(check("seo", "https", "HTTPS", "pass" if final_url.startswith("https://") else "fail", 8,
              "Served over HTTPS." if final_url.startswith("https://") else "Page is served over HTTP.",
              "" if final_url.startswith("https://") else "Install a TLS certificate and redirect HTTP to HTTPS."))

    vp = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    add(check("seo", "viewport", "Mobile viewport", "pass" if vp else "fail", 6,
              "Viewport meta present." if vp else "No viewport meta tag.",
              "" if vp else "Add a viewport tag so the page is mobile-friendly.",
              "" if vp else '<meta name="viewport" content="width=device-width, initial-scale=1">', "html"))

    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None
    add(check("seo", "lang", "Language attribute", "pass" if lang else "warn", 2,
              f"lang=\"{lang}\"" if lang else "<html> has no lang attribute.",
              "" if lang else 'Add lang="en" (or your language) to <html>.'))

    og = soup.find("meta", property="og:title")
    add(check("seo", "og", "Open Graph tags", "pass" if og else "warn", 3,
              "Open Graph tags found." if og else "No Open Graph tags.",
              "" if og else "Add OG tags for better link previews on social and in AI chat UIs.",
              "" if og else f'<meta property="og:title" content="{first_sentence(title or topic, 60)}">\n'
                            f'<meta property="og:description" content="{first_sentence(meta_desc or (paragraphs[0] if paragraphs else topic), 150)}">\n'
                            f'<meta property="og:url" content="{final_url}">', "html"))

    robots_meta = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    noindex = bool(robots_meta and "noindex" in (robots_meta.get("content") or "").lower())
    add(check("seo", "noindex", "Indexability", "fail" if noindex else "pass", 8,
              "Page has a noindex directive." if noindex else "Page is indexable.",
              "Remove noindex if you want this page to rank." if noindex else ""))

    sm_ok = (sitemap is not None and sitemap.status_code == 200 and
             ("<urlset" in sitemap.text.lower() or "<sitemapindex" in sitemap.text.lower()))
    add(check("seo", "sitemap", "XML sitemap", "pass" if sm_ok else "warn", 4,
              "/sitemap.xml found." if sm_ok else "/sitemap.xml not found.",
              "" if sm_ok else "Publish a sitemap and reference it in robots.txt.",
              "" if sm_ok else f"Sitemap: {base}/sitemap.xml", "text"))

    add(check("seo", "wordcount", "Content depth", "pass" if wc >= 300 else "warn", 6,
              f"About {wc} words of body text.",
              "" if wc >= 300 else "Thin pages rarely rank or get cited. Expand with useful, specific content (300+ words)."))

    host = urlparse(final_url).netloc
    links = [a.get("href", "") for a in soup.find_all("a", href=True)]
    internal = [l for l in links if l.startswith("/") or host in l]
    add(check("seo", "internal", "Internal links", "pass" if len(internal) >= 3 else "warn", 3,
              f"{len(internal)} internal links.",
              "" if len(internal) >= 3 else "Link to related pages to help crawlers and users navigate."))

    # ------------------------------ AEO ---------------------------------
    answer_types = {"FAQPage", "HowTo", "QAPage"}
    content_types = {"Article", "BlogPosting", "NewsArticle", "Product", "LocalBusiness", "Service"}
    qh = [(lvl, t) for lvl, t in headings if lvl in (2, 3) and (t.endswith("?") or QUESTION_RE.match(t))]

    # Collect Q&A pairs for FAQ generation
    pairs = []
    for h in soup.find_all(["h2", "h3"]):
        t = h.get_text(" ", strip=True)
        if not (t.endswith("?") or QUESTION_RE.match(t)):
            continue
        nxt = h.find_next(["p", "ul", "ol"])
        if nxt:
            pairs.append((t, nxt.get_text(" ", strip=True), nxt.name))

    faq_code = ""
    if pairs:
        faq = {
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q if q.endswith("?") else q + "?",
                 "acceptedAnswer": {"@type": "Answer", "text": first_sentence(a, 300)}}
                for q, a, _ in pairs[:8]
            ],
        }
        faq_code = '<script type="application/ld+json">\n' + json.dumps(faq, indent=2, ensure_ascii=False) + "\n</script>"

    if answer_types & ld_types:
        add(check("aeo", "faqschema", "Answer-ready schema", "pass", 18,
                  f"Found: {', '.join(sorted(answer_types & ld_types))}."))
    elif ld_types & content_types:
        add(check("aeo", "faqschema", "Answer-ready schema", "warn", 18,
                  f"Schema found ({', '.join(sorted(ld_types))}) but no FAQPage/HowTo/QAPage.",
                  "Add FAQPage markup for the questions your page answers.", faq_code, "html"))
    else:
        add(check("aeo", "faqschema", "Answer-ready schema", "fail", 18,
                  "No FAQPage, HowTo or QAPage structured data." if not ld_types else f"Only: {', '.join(sorted(ld_types))}.",
                  "Add FAQPage JSON-LD. A draft built from your question headings is below." if faq_code
                  else "Add a short FAQ section (3–6 real customer questions) and mark it up with FAQPage JSON-LD.",
                  faq_code, "html"))

    if len(qh) >= 3:
        add(check("aeo", "qheadings", "Question-style headings", "pass", 14, f"{len(qh)} question headings, e.g. “{qh[0][1]}”."))
    elif qh:
        add(check("aeo", "qheadings", "Question-style headings", "warn", 14, f"Only {len(qh)} question heading(s).",
                  "Add more H2/H3 headings phrased the way people ask, e.g. “How much does X cost?”."))
    else:
        add(check("aeo", "qheadings", "Question-style headings", "fail", 14, "No question-style H2/H3 headings.",
                  "Rewrite some headings as real questions and answer each directly underneath.",
                  f"<h2>What is {topic}?</h2>\n<p>{topic} is … (answer in 40–60 words)</p>", "html"))

    long_answers = [(q, a) for q, a, tag in pairs if tag == "p" and words(a) > 60]
    if not pairs:
        add(check("aeo", "capsule", "Direct-answer capsules", "fail", 16,
                  "No question headings, so no direct answers can be extracted.",
                  "Under each question heading, open with a 40–60 word self-contained answer."))
    elif long_answers:
        q, a = long_answers[0]
        add(check("aeo", "capsule", "Direct-answer capsules", "warn", 16,
                  f"{len(long_answers)} answer(s) open with more than 60 words, e.g. under “{q}”.",
                  "Lead with a short standalone answer (40–60 words), then elaborate.",
                  f"Q: {q}\nCurrent opening: {first_sentence(a, 200)}\nRewrite as: <one-sentence definition> + <key fact> + <next step> (≤ 60 words).", "text"))
    else:
        add(check("aeo", "capsule", "Direct-answer capsules", "pass", 16, "Answers under question headings are concise."))

    structured = len(soup.find_all(["ul", "ol", "table"]))
    add(check("aeo", "structure", "Lists and tables", "pass" if structured >= 2 else "warn", 8,
              f"{structured} list/table element(s).",
              "" if structured >= 2 else "Use bullet lists, numbered steps and comparison tables; they are the easiest formats to quote."))

    dated = bool(soup.find("time")) or any("dateModified" in n or "datePublished" in n for n in jsonld)
    add(check("aeo", "fresh", "Freshness signal", "pass" if dated else "warn", 6,
              "Publish/update date found." if dated else "No visible date or dateModified.",
              "" if dated else "Show a “Last updated” date and include dateModified in your schema."))

    # ------------------------------ GEO ---------------------------------
    groups = parse_robots(robots.text) if robots is not None and robots.status_code == 200 else {}
    blocked = [b for b in AI_BOTS if bot_blocked(groups, b)] if groups else []
    if robots is None or robots.status_code != 200:
        add(check("geo", "bots", "AI crawler access", "warn", 20, "No robots.txt found; crawlers are allowed by default.",
                  "Add a robots.txt that explicitly welcomes AI search bots.",
                  "User-agent: OAI-SearchBot\nAllow: /\n\nUser-agent: ClaudeBot\nAllow: /\n\nUser-agent: PerplexityBot\nAllow: /\n\nUser-agent: Google-Extended\nAllow: /", "text"))
    elif blocked:
        snippet = "\n\n".join(f"User-agent: {b}\nAllow: /" for b in blocked)
        add(check("geo", "bots", "AI crawler access", "fail", 20, f"Blocked: {', '.join(blocked)}.",
                  "Blocked bots cannot cite you. Allow the ones you want (training bots like GPTBot/CCBot are a business choice; search bots should be allowed).",
                  snippet, "text"))
    else:
        add(check("geo", "bots", "AI crawler access", "pass", 20, "No AI crawlers are blocked in robots.txt."))

    llms_ok = llms is not None and llms.status_code == 200 and "<html" not in llms.text[:500].lower() and len(llms.text.strip()) > 20
    nav_links = []
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True)
        if txt and not a["href"].startswith(("#", "mailto:", "tel:", "javascript:")):
            nav_links.append((txt, urljoin(final_url, a["href"])))
    seen, pages = set(), []
    for txt, href in nav_links:
        if href not in seen and urlparse(href).netloc == urlparse(final_url).netloc:
            seen.add(href)
            pages.append((txt, href))
    llms_draft = f"# {brand}\n\n> {first_sentence(meta_desc or (paragraphs[0] if paragraphs else topic), 200)}\n\n## Key pages\n" + \
        "\n".join(f"- [{t}]({h})" for t, h in pages[:10])
    add(check("geo", "llms", "llms.txt", "pass" if llms_ok else "warn", 10,
              "/llms.txt found." if llms_ok else "/llms.txt not found.",
              "" if llms_ok else "Publish /llms.txt (Markdown) that summarises the site and links to key pages. It is an emerging convention; no major engine guarantees it, so treat it as low-cost insurance.",
              "" if llms_ok else llms_draft, "markdown"))

    orgs = [n for n in jsonld if types_of([n]) & {"Organization", "LocalBusiness", "Person", "Corporation"}]
    same_as = []
    for n in orgs:
        s = n.get("sameAs")
        same_as += s if isinstance(s, list) else ([s] if s else [])
    social = sorted({urlparse(h).netloc.replace("www.", "") for _, h in nav_links if any(s in h for s in SOCIAL_HOSTS)})
    org_code = '<script type="application/ld+json">\n' + json.dumps({
        "@context": "https://schema.org", "@type": "Organization", "name": brand, "url": base,
        "description": first_sentence(meta_desc or topic, 200),
        "sameAs": [h for _, h in nav_links if any(s in h for s in SOCIAL_HOSTS)][:6] or
                  ["https://www.linkedin.com/company/your-company", "https://en.wikipedia.org/wiki/Your_Company"],
    }, indent=2, ensure_ascii=False) + "\n</script>"
    if orgs and same_as:
        add(check("geo", "entity", "Entity clarity (Organization + sameAs)", "pass", 18, f"Organization schema with {len(same_as)} sameAs link(s)."))
    elif orgs:
        add(check("geo", "entity", "Entity clarity (Organization + sameAs)", "warn", 18, "Organization schema exists but has no sameAs links.",
                  "Link your brand to official profiles so AI can resolve who you are.", org_code, "html"))
    else:
        add(check("geo", "entity", "Entity clarity (Organization + sameAs)", "fail", 18, "No Organization/Person schema." + (f" Social links on page: {', '.join(social)}." if social else ""),
                  "Add Organization JSON-LD with sameAs links so AI engines can ground your brand as an entity.", org_code, "html"))

    author = bool(soup.find(attrs={"rel": "author"}) or soup.find(attrs={"name": re.compile("author", re.I)})
                  or soup.find(class_=re.compile("author|byline", re.I)) or any("author" in n for n in jsonld))
    add(check("geo", "author", "Author / E-E-A-T attribution", "pass" if author else "warn", 10,
              "Author or byline found." if author else "No visible author or byline.",
              "" if author else "Show who wrote or reviewed the content, with credentials, and mark it up with author schema."))

    stats = len(STAT_RE.findall(body_text))
    per500 = round(stats / max(wc, 1) * 500, 1)
    ext_links = [l for l in links if l.startswith("http") and host not in l]
    if per500 >= 3 and ext_links:
        add(check("geo", "citations", "Fact density and citations", "pass", 16, f"{per500} data points per 500 words, {len(ext_links)} outbound links."))
    else:
        add(check("geo", "citations", "Fact density and citations", "warn" if (per500 >= 1 or ext_links) else "fail", 16,
                  f"{per500} data points per 500 words, {len(ext_links)} outbound link(s).",
                  "Generative engines prefer pages with specific, verifiable facts. Add original numbers, named sources and links to authoritative references."))

    raw_words = words(BeautifulSoup(page.text, "html.parser").get_text(" "))
    shell = bool(soup.find(id=re.compile(r"^(root|app|__next|__nuxt)$"))) or len(soup.find_all("script", src=True)) >= 3
    js_heavy = raw_words < 150 and shell
    add(check("geo", "rendering", "Content visible without JavaScript", "fail" if js_heavy else "pass", 10,
              f"Only {raw_words} words in the initial HTML; most AI crawlers do not run JavaScript." if js_heavy
              else f"{raw_words} words available in raw HTML.",
              "Server-render or pre-render key content so crawlers see it." if js_heavy else ""))

    about = any(re.search(r"about|contact|team|who we are", t, re.I) for t, _ in nav_links)
    add(check("geo", "trust", "Trust pages (About / Contact)", "pass" if about else "warn", 6,
              "About/Contact links found." if about else "No About or Contact link.",
              "" if about else "Link clear About and Contact pages; they help engines verify the entity behind the content."))

    scores = {k: score(checks, k) for k in ("seo", "aeo", "geo")}
    scores["overall"] = round(sum(scores.values()) / 3)
    return {
        "url": final_url,
        "title": title,
        "scores": scores,
        "checks": checks,
        "summary": {
            "pass": sum(c["status"] == "pass" for c in checks),
            "warn": sum(c["status"] == "warn" for c in checks),
            "fail": sum(c["status"] == "fail" for c in checks),
        },
        "notes": "Rule-based audit of the initial HTML. JavaScript-rendered content and Core Web Vitals are not measured.",
    }
