"""Extra audit pillars: technical SEO extras, agent readiness, agentic commerce, CRO/UX.

Each function takes a context dict `c` built in auditor.audit() and returns a list of checks.
"""
import json
import re
from urllib.parse import urlparse

CTA_RE = re.compile(
    r"\b(get|buy|book|contact|start|sign\s?up|try|request|shop|order|call|subscribe|download|"
    r"schedule|learn more|get started|add to cart|quote|demo|join)\b", re.I)
TRUST_RE = re.compile(
    r"testimonial|reviews?\b|customers?\b|clients?\b|trusted by|as seen|case stud|rated|guarantee|certified|awards?\b", re.I)
CAPTCHA_RE = re.compile(r"recaptcha|hcaptcha|turnstile|captcha", re.I)
PRICE_RE = re.compile(r"(?:[$€£₹]|USD|EUR|GBP|INR|Rs\.?)\s?\d[\d,]*(?:\.\d{1,2})?")
CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}


def chk(pillar, cid, title, status, weight, detail, solution="", code="", lang=""):
    return {"pillar": pillar, "id": cid, "title": title, "status": status, "weight": weight,
            "detail": detail, "solution": solution, "code": code, "lang": lang}


def _words(t):
    return len(re.findall(r"\w+", t))


def _types(node):
    t = node.get("@type")
    return set(t) if isinstance(t, list) else ({t} if t else set())


def _trim(text, limit):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


# ----------------------------------------------------------------------------
# Technical SEO extras (pillar: seo)
# ----------------------------------------------------------------------------
def tech_checks(c):
    out, page, soup = [], c["page"], c["soup"]
    hops = len(page.history)
    out.append(chk("seo", "redirects", "Redirect chain", "pass" if hops <= 1 else "warn", 4,
                   f"{hops} redirect(s) before the final URL.",
                   "" if hops <= 1 else "Link straight to the final URL; long chains waste crawl budget and slow the page."))

    robots_txt = c["robots_text"]
    if not robots_txt:
        out.append(chk("seo", "robots", "robots.txt", "warn", 4, "No robots.txt found.",
                       "Add a robots.txt that points to your sitemap.",
                       f"User-agent: *\nAllow: /\n\nSitemap: {c['base']}/sitemap.xml", "text"))
    elif "sitemap:" not in robots_txt.lower():
        out.append(chk("seo", "robots", "robots.txt sitemap reference", "warn", 4,
                       "robots.txt does not reference a sitemap.", "Add a Sitemap line.",
                       f"Sitemap: {c['base']}/sitemap.xml", "text"))
    else:
        out.append(chk("seo", "robots", "robots.txt", "pass", 4, "robots.txt references a sitemap."))

    scripts = soup.find_all("script", type="application/ld+json")
    bad = c["ld_errors"]
    out.append(chk("seo", "ldvalid", "Structured data validity", "fail" if bad else "pass", 5,
                   f"{bad} of {len(scripts)} JSON-LD block(s) contain invalid JSON." if bad
                   else (f"{len(scripts)} JSON-LD block(s) parse correctly." if scripts else "No JSON-LD blocks."),
                   "Fix the JSON syntax (trailing commas and unescaped quotes are the usual causes); invalid blocks are ignored entirely." if bad else ""))

    xr = page.headers.get("x-robots-tag", "").lower()
    if "noindex" in xr:
        out.append(chk("seo", "xrobots", "X-Robots-Tag header", "fail", 6,
                       f"Response header blocks indexing: {xr}.", "Remove noindex from the X-Robots-Tag header if the page should rank."))

    url = c["final_url"]
    q = urlparse(url).query
    clean = len(url) <= 115 and q.count("&") < 2
    out.append(chk("seo", "urlclean", "URL cleanliness", "pass" if clean else "warn", 2,
                   "URL is short and clean." if clean else f"URL is long or parameter-heavy ({len(url)} chars).",
                   "" if clean else "Prefer short, readable, parameter-free URLs."))

    icon = soup.find("link", rel=lambda v: v and "icon" in " ".join(v).lower())
    out.append(chk("seo", "favicon", "Favicon", "pass" if icon else "warn", 2,
                   "Favicon declared." if icon else "No favicon link found.",
                   "" if icon else "Add a favicon; it shows in search results on mobile.",
                   "" if icon else '<link rel="icon" href="/favicon.ico">', "html"))

    langs = [l.get("hreflang") for l in soup.find_all("link", hreflang=True)]
    if langs:
        ok = "x-default" in langs
        out.append(chk("seo", "hreflang", "hreflang", "pass" if ok else "warn", 3,
                       f"{len(langs)} hreflang link(s)" + ("" if ok else ", but no x-default."),
                       "" if ok else "Add an x-default hreflang for users whose language you don't target.",
                       "" if ok else f'<link rel="alternate" hreflang="x-default" href="{url}">', "html"))

    size = len(page.content)
    enc = page.headers.get("content-encoding", "").lower()
    out.append(chk("seo", "size", "HTML size", "pass" if size <= 500_000 else ("warn" if size <= 1_500_000 else "fail"), 4,
                   f"HTML is {size // 1024} KB.", "" if size <= 500_000 else "Trim inline scripts/styles and large embedded data."))
    out.append(chk("seo", "compress", "Compression", "pass" if enc in ("gzip", "br", "zstd", "deflate") else "warn", 4,
                   f"Content-Encoding: {enc or 'none'}.",
                   "" if enc else "Enable gzip or Brotli on your server/CDN (Vercel, Cloudflare and Netlify do this by default)."))
    return out


# ----------------------------------------------------------------------------
# Agent readiness (pillar: agent)
# ----------------------------------------------------------------------------
WEBMCP_SNIPPET = """<script>
// Experimental: WebMCP lets agents call named tools on your page.
// The spec is still a draft, so check the current API before shipping.
if ("modelContext" in navigator) {
  navigator.modelContext.registerTool({
    name: "search_site",
    description: "Search this website and return matching pages.",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"]
    },
    async execute({ query }) {
      const r = await fetch("/search?q=" + encodeURIComponent(query));
      return { content: [{ type: "text", text: await r.text() }] };
    }
  });
}
</script>"""


def agent_checks(c):
    out, soup, raw = [], c["soup"], c["raw_html"]
    low = raw.lower()

    has_tools = "modelcontext" in low or "toolname=" in low or "nlweb" in low
    out.append(chk("agent", "tools", "Agent tools (WebMCP / NLWeb)", "pass" if has_tools else "warn", 12,
                   "Found WebMCP or NLWeb signals." if has_tools else
                   "No WebMCP or NLWeb signals. These are emerging standards, so absence is normal today.",
                   "" if has_tools else "Expose your key actions (search, booking, add to cart) as named tools so agents don't have to imitate clicks. Both standards are still experimental.",
                   "" if has_tools else WEBMCP_SNIPPET, "html"))

    g = c["robots_groups"].get("*", [])
    blocked = any(d == "disallow" and v == "/" for d, v in g) and not any(d == "allow" and v in ("/", "") for d, v in g)
    out.append(chk("agent", "crawl", "Open to agents (robots.txt *)", "fail" if blocked else "pass", 10,
                   "robots.txt blocks all crawlers." if blocked else "Generic crawlers/agents are not blocked.",
                   "Agents that respect robots.txt cannot use a site that disallows everything." if blocked else ""))

    label_for = {l.get("for") for l in soup.find_all("label") if l.get("for")}
    fields = [i for i in soup.find_all(["input", "select", "textarea"])
              if i.name != "input" or (i.get("type") or "text").lower() not in ("hidden", "submit", "button", "reset", "image")]
    unl = [i for i in fields if not (i.get("aria-label") or i.get("aria-labelledby") or i.get("title")
                                     or (i.get("id") and i["id"] in label_for) or i.find_parent("label"))]
    if not fields:
        out.append(chk("agent", "labels", "Labelled form fields", "pass", 12, "No form fields on this page."))
    else:
        ratio = 1 - len(unl) / len(fields)
        out.append(chk("agent", "labels", "Labelled form fields", "pass" if not unl else ("warn" if ratio >= .7 else "fail"), 12,
                       f"{len(unl)} of {len(fields)} field(s) have no programmatic label." if unl else f"All {len(fields)} fields are labelled.",
                       "" if not unl else "Agents (and screen readers) identify fields by label, name and autocomplete, not by placeholder text.",
                       "" if not unl else '<label for="email">Email</label>\n<input id="email" name="email" type="email" autocomplete="email">', "html"))

    def has_name(el):
        if el.get_text(strip=True) or el.get("aria-label") or el.get("title") or el.get("value"):
            return True
        img = el.find("img")
        return bool(img and (img.get("alt") or "").strip())
    ctrls = soup.find_all(["button", "a"])
    nameless = [e for e in ctrls if e.name == "button" and not has_name(e)] + \
               [e for e in ctrls if e.name == "a" and e.get("href") and not has_name(e)]
    out.append(chk("agent", "names", "Named buttons and links", "pass" if not nameless else ("warn" if len(nameless) <= 3 else "fail"), 8,
                   f"{len(nameless)} button(s)/link(s) have no text or aria-label." if nameless else "All buttons and links have accessible names.",
                   "" if not nameless else "Give icon-only controls an aria-label, e.g. <button aria-label=\"Add to cart\">."))

    fake = [e for e in soup.find_all(["div", "span"]) if e.get("onclick") and not (e.get("role") or e.get("tabindex"))]
    out.append(chk("agent", "semantic", "Real controls, not clickable divs", "pass" if not fake else "warn", 6,
                   f"{len(fake)} clickable <div>/<span> element(s) without a role." if fake else "No fake buttons found.",
                   "" if not fake else "Use <button> or <a>; agents and assistive tech only recognise real controls."))

    cap = bool(CAPTCHA_RE.search(raw))
    out.append(chk("agent", "captcha", "Bot challenges (CAPTCHA)", "warn" if cap else "pass", 6,
                   "A CAPTCHA/bot challenge script was detected." if cap else "No CAPTCHA detected.",
                   "" if not cap else "Challenges block legitimate agents too. Protect only the sensitive step (e.g. final submit) and offer an API or verified-agent path where you can."))

    alt = soup.find("link", rel=lambda v: v and "alternate" in " ".join(v).lower(),
                    type=lambda t: t and any(x in t for x in ("rss", "atom", "json")))
    openapi = any("openapi" in (a.get("href") or "").lower() for a in soup.find_all("a", href=True))
    card = c["agent_card"]
    found = bool(alt or openapi or card)
    out.append(chk("agent", "feeds", "Machine-readable feeds / agent card", "pass" if found else "warn", 6,
                   "Found " + ", ".join(x for x, ok in (("feed", alt), ("OpenAPI link", openapi), ("agent card", card)) if ok) + "." if found
                   else "No RSS/Atom/JSON feed, OpenAPI link or /.well-known/agent-card.json.",
                   "" if found else "Offer a feed or API description so agents can read your data without scraping. The agent-card convention is emerging; a feed is the safe first step.",
                   "" if found else '<link rel="alternate" type="application/rss+xml" title="Feed" href="/feed.xml">', "html"))

    forms = soup.find_all("form")
    noact = [f for f in forms if not f.get("action")]
    if forms:
        out.append(chk("agent", "formaction", "Forms submit without JavaScript", "pass" if not noact else "warn", 3,
                       f"{len(noact)} of {len(forms)} form(s) have no action attribute." if noact else "All forms declare an action.",
                       "" if not noact else "Forms that rely on JavaScript alone are harder for agents to submit. Add action and method where possible."))
    return out


# ----------------------------------------------------------------------------
# Agentic commerce (pillar: commerce) - only if the page looks like a store
# ----------------------------------------------------------------------------
def detect_store(c):
    raw = c["raw_html"].lower()
    prods = [n for n in c["jsonld"] if "Product" in _types(n)]
    og = c["soup"].find("meta", property="og:type")
    og_prod = bool(og and "product" in (og.get("content") or "").lower())
    cart = "add to cart" in c["full_text"].lower() or "add to bag" in c["full_text"].lower()
    platform = ("Shopify" if "cdn.shopify.com" in raw or "shopify.theme" in raw else
                "WooCommerce" if "woocommerce" in raw else
                "BigCommerce" if "bigcommerce.com" in raw else None)
    return bool(prods or og_prod or cart or platform), platform, prods


def commerce_checks(c, prods, platform):
    out, soup = [], c["soup"]
    prod = prods[0] if prods else {}
    offers = []
    o = prod.get("offers")
    if isinstance(o, dict):
        offers = [o]
    elif isinstance(o, list):
        offers = [x for x in o if isinstance(x, dict)]

    # template for the Product schema, built from the page
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else (c["title"] or c["topic"])
    ogimg = soup.find("meta", property="og:image")
    img = (ogimg.get("content") if ogimg else None) or (soup.find("img", src=True) or {}).get("src", "https://example.com/product.jpg")
    m = PRICE_RE.search(c["full_text"])
    price, cur = "0.00", "USD"
    if m:
        num = re.search(r"\d[\d,]*(?:\.\d{1,2})?", m.group()).group().replace(",", "")
        price = num
        sym = re.match(r"[$€£₹]", m.group())
        cur = CURRENCY.get(sym.group(), "USD") if sym else (re.match(r"[A-Z]{3}", m.group()) or [None])[0] or "USD"
    tmpl = {"@context": "https://schema.org", "@type": "Product", "name": name, "image": [img],
            "description": _trim(c["meta_desc"] or (c["paragraphs"][0] if c["paragraphs"] else name), 200),
            "sku": "YOUR-SKU", "brand": {"@type": "Brand", "name": c["brand"]},
            "offers": {"@type": "Offer", "url": c["final_url"], "price": price, "priceCurrency": cur,
                       "availability": "https://schema.org/InStock", "itemCondition": "https://schema.org/NewCondition"}}
    tcode = '<script type="application/ld+json">\n' + json.dumps(tmpl, indent=2, ensure_ascii=False) + "\n</script>"

    out.append(chk("commerce", "product", "Product schema", "pass" if prod else "fail", 20,
                   "Product structured data found." if prod else "No Product JSON-LD. Agents cannot reliably read price, stock or options.",
                   "" if prod else "Add Product markup. A draft built from this page is below; replace the placeholder values (sku, price, stock).",
                   "" if prod else tcode, "html"))

    core = [k for k in ("name", "image", "description") if not prod.get(k)]
    out.append(chk("commerce", "core", "Name, image, description", "pass" if prod and not core else "fail", 8,
                   "All present." if prod and not core else f"Missing: {', '.join(core) if prod else 'no Product schema'}.",
                   "" if prod and not core else "Fill in the core product fields."))

    def price_of(x):
        spec = x.get("priceSpecification")
        return x.get("price") or x.get("lowPrice") or (spec.get("price") if isinstance(spec, dict) else None)
    good = [x for x in offers if price_of(x) and x.get("priceCurrency") and x.get("availability")]
    miss = []
    if offers:
        x = offers[0]
        miss = [k for k, v in (("price", price_of(x)), ("priceCurrency", x.get("priceCurrency")), ("availability", x.get("availability"))) if not v]
    out.append(chk("commerce", "offer", "Price, currency and availability", "pass" if good else ("warn" if offers else "fail"), 16,
                   "Offer has price, currency and availability." if good else
                   (f"Offer is missing: {', '.join(miss)}." if offers else "No Offer data."),
                   "" if good else "Agents compare and filter on these three fields. Keep them in sync with the visible page, or the listing may be rejected.",
                   "" if good else '"offers": {\n  "@type": "Offer",\n  "price": "49.00",\n  "priceCurrency": "USD",\n  "availability": "https://schema.org/InStock"\n}', "json"))

    has_rev = bool(prod.get("aggregateRating") or prod.get("review"))
    out.append(chk("commerce", "reviews", "Ratings and reviews markup", "pass" if has_rev else "warn", 8,
                   "Rating/review data found." if has_rev else "No aggregateRating or review markup.",
                   "" if has_rev else "Only mark up real reviews shown on the page. Agents weigh ratings heavily when ranking options.",
                   "" if has_rev else '"aggregateRating": {\n  "@type": "AggregateRating",\n  "ratingValue": "4.6",\n  "reviewCount": "128"\n}', "json"))

    ident = bool(prod.get("sku") or prod.get("mpn") or any(k.startswith("gtin") for k in prod))
    brand = bool(prod.get("brand"))
    out.append(chk("commerce", "ids", "SKU / GTIN and brand", "pass" if ident and brand else ("warn" if ident or brand else "fail"), 8,
                   f"{'Identifier' if ident else 'No identifier (sku/gtin/mpn)'}; {'brand present' if brand else 'no brand'}.",
                   "" if ident and brand else "Identifiers let agents match your product across stores and feeds."))

    policy = any("hasMerchantReturnPolicy" in x or "shippingDetails" in x for x in offers + [prod])
    out.append(chk("commerce", "policy", "Return and shipping data", "pass" if policy else "warn", 6,
                   "Return/shipping markup found." if policy else "No hasMerchantReturnPolicy or shippingDetails markup.",
                   "" if policy else "Agents need to know shipping cost and return terms before recommending a purchase.",
                   "" if policy else '"hasMerchantReturnPolicy": {\n  "@type": "MerchantReturnPolicy",\n  "applicableCountry": "US",\n  "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",\n  "merchantReturnDays": 30\n}', "json"))

    visible = bool(PRICE_RE.search(c["full_text"]))
    sp = price_of(offers[0]) if offers else None
    if sp and visible:
        txt = c["full_text"].replace(",", "")
        match = str(sp).replace(",", "") in txt
        out.append(chk("commerce", "consistent", "Schema price matches visible price", "pass" if match else "warn", 6,
                       f"Schema price {sp} " + ("appears on the page." if match else "was not found in the visible text."),
                       "" if match else "A mismatch between markup and page is a common reason shopping surfaces reject feeds."))
    else:
        out.append(chk("commerce", "visibleprice", "Visible price", "pass" if visible else "warn", 6,
                       "A price is visible in the page text." if visible else "No price found in the page text.",
                       "" if visible else "Show the price in plain HTML text, not only in an image or after JavaScript loads."))

    pol_link = any(re.search(r"return|refund|shipping|delivery", a.get_text(" ", strip=True), re.I) for a in soup.find_all("a", href=True))
    out.append(chk("commerce", "policylink", "Policy pages linked", "pass" if pol_link else "warn", 5,
                   "Returns/shipping link found." if pol_link else "No returns, refund or shipping link.",
                   "" if pol_link else "Link clear policy pages; agents and shoppers both look for them."))
    return out


# ----------------------------------------------------------------------------
# CRO / UX (pillar: ux)
# ----------------------------------------------------------------------------
def ux_checks(c):
    out, soup = [], c["soup"]
    ctas = []
    for el in soup.find_all(["button", "a"])[:25]:
        t = el.get_text(" ", strip=True)
        if t and len(t) <= 40 and CTA_RE.search(t):
            ctas.append(t)
    out.append(chk("ux", "cta", "Clear call to action", "pass" if ctas else "fail", 14,
                   f"Found CTA(s) near the top: “{ctas[0]}”." if ctas else "No clear call-to-action among the first 25 buttons/links.",
                   "" if ctas else "Give every page one obvious next step (Book a demo, Get a quote, Add to cart).",
                   "" if ctas else '<a class="btn" href="/contact">Get a free quote</a>', "html"))

    h1 = soup.find("h1")
    n = _words(h1.get_text(" ")) if h1 else 0
    out.append(chk("ux", "value", "Headline clarity", "pass" if 3 <= n <= 14 else "warn", 6,
                   f"H1 has {n} words." if h1 else "No H1.",
                   "" if 3 <= n <= 14 else "State what you offer and for whom in 3–14 words."))

    contact = bool(soup.select('a[href^="tel:"], a[href^="mailto:"]') or soup.find("form"))
    out.append(chk("ux", "contact", "Easy way to get in touch", "pass" if contact else "fail", 8,
                   "Phone, email or a form is available." if contact else "No tel:, mailto: or form found.",
                   "" if contact else "Add a visible contact route.", '<a href="tel:+10000000000">Call us</a>' if not contact else "", "html"))

    forms = soup.find_all("form")
    if forms:
        most = max(len([i for i in f.find_all(["input", "select", "textarea"])
                        if i.name != "input" or (i.get("type") or "text").lower() not in ("hidden", "submit", "button", "checkbox", "reset")])
                   for f in forms)
        out.append(chk("ux", "formlen", "Form length", "pass" if most <= 6 else "warn", 8,
                       f"Longest form has {most} field(s).",
                       "" if most <= 6 else "Each extra field lowers completion. Ask only what you need now."))

    trust = len(TRUST_RE.findall(c["full_text"]))
    out.append(chk("ux", "trust", "Social proof and trust signals", "pass" if trust >= 2 else "warn", 8,
                   f"{trust} trust-related mention(s) (reviews, clients, guarantees…).",
                   "" if trust >= 2 else "Add testimonials, client logos, ratings or guarantees near your call to action."))

    head = soup.find("head")
    blocking = [s for s in (head.find_all("script", src=True) if head else [])
                if not (s.get("async") is not None or s.get("defer") is not None or s.get("type") == "module")]
    out.append(chk("ux", "blocking", "Render-blocking scripts", "pass" if len(blocking) <= 2 else "warn", 8,
                   f"{len(blocking)} blocking script(s) in <head>.",
                   "" if len(blocking) <= 2 else "Add defer or async to head scripts so the page paints sooner.",
                   "" if len(blocking) <= 2 else '<script src="app.js" defer></script>', "html"))

    imgs = soup.find_all("img")
    nodim = [i for i in imgs if not (i.get("width") and i.get("height"))]
    out.append(chk("ux", "dims", "Image dimensions (layout shift)", "pass" if len(nodim) <= max(1, len(imgs) // 2) else "warn", 6,
                   f"{len(nodim)} of {len(imgs)} images lack width/height." if imgs else "No images.",
                   "" if len(nodim) <= max(1, len(imgs) // 2) else "Set width and height so the browser reserves space and the page doesn't jump."))
    lazy = [i for i in imgs[2:] if (i.get("loading") or "").lower() != "lazy"]
    out.append(chk("ux", "lazy", "Lazy-loaded images", "pass" if len(lazy) <= 3 else "warn", 4,
                   f"{len(lazy)} below-the-fold image(s) load eagerly.",
                   "" if len(lazy) <= 3 else 'Add loading="lazy" to images below the first screen.'))

    sents = [s for p in c["paragraphs"] for s in re.split(r"(?<=[.!?])\s+", p) if _words(s) >= 3]
    if len(sents) >= 5:
        avg = sum(_words(s) for s in sents) / len(sents)
        out.append(chk("ux", "readability", "Readability", "pass" if avg <= 22 else ("warn" if avg <= 30 else "fail"), 6,
                       f"Average sentence length is {avg:.0f} words.",
                       "" if avg <= 22 else "Shorter sentences convert better and are easier for AI to quote. Aim for under 22 words."))

    heads = len(soup.find_all(["h2", "h3"]))
    per = c["wc"] / max(heads, 1)
    out.append(chk("ux", "scan", "Scannability", "pass" if per <= 250 or c["wc"] < 250 else "warn", 4,
                   f"About {per:.0f} words per subheading.",
                   "" if per <= 250 or c["wc"] < 250 else "Break long sections up with subheadings, lists and short paragraphs."))
    return out
