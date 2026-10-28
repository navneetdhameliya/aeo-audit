# SEO · AEO · GEO · Agent Readiness Audit

Paste a URL, get a report with scores and copy-paste fixes across six areas.
No API key needed: checks are rule-based and fixes are generated from the page's own content.

## Tabs

| Tab | What it covers |
|---|---|
| **SEO** | On-page and technical SEO: title, meta, canonical, H1, headings, alt text, HTTPS, viewport, OG, noindex and X-Robots-Tag, sitemap, robots.txt, redirects, JSON-LD validity, hreflang, favicon, page size, compression |
| **AEO** | FAQPage/HowTo schema, question headings, ≤60-word direct answers, lists/tables, freshness |
| **GEO** | AI crawler access, llms.txt, Organization + sameAs, author/E-E-A-T, fact density, content without JavaScript, trust pages |
| **Agent** | WebMCP/NLWeb signals, open to agents, labelled form fields, named buttons, real controls, CAPTCHA, feeds/agent card, forms without JS |
| **UX** | CTA, headline clarity, contact route, form length, social proof, render-blocking scripts, image dimensions, lazy loading, readability, scannability |
| **Commerce** | Shown only when a store is detected: Product schema, offer (price/currency/availability), reviews, SKU/GTIN/brand, return and shipping data, price consistency, policy links |

Not measured: off-page SEO (backlinks need a link-data API), Core Web Vitals, JavaScript-rendered content, real conversion data.
WebMCP, NLWeb and agent cards are emerging standards, so those checks only warn.

## Project layout

```
api/index.py     FastAPI app (Vercel serverless function, catch-all route)
api/auditor.py   Core engine: SEO, AEO, GEO checks, scoring
api/extras.py    Technical SEO extras, Agent, Commerce, UX checks
public/index.html Frontend
vercel.json      Function timeout
requirements.txt Dependencies
```

## Run locally

```bash
npm i -g vercel
vercel dev        # UI + API at http://localhost:3000
```

or API only:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```

## Deploy

```bash
git add . && git commit -m "Add agent, UX and commerce audits" && git push
```
Vercel redeploys automatically from GitHub.

## Notes

- Private, loopback and link-local addresses are blocked (SSRF). For local tests only: `ALLOW_PRIVATE=1`.
- Add rate limiting before sharing the public URL widely.
