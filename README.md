# SEO · AEO · GEO Audit Tool

Paste a URL, get a three-part report (SEO, AEO, GEO) with scores and copy-paste fixes.
No API key needed: checks are rule-based and fixes are generated from the page's own content.

## Project layout

```
api/index.py      FastAPI app (Vercel serverless function)
api/auditor.py    Audit engine
public/index.html Frontend (served statically at /)
vercel.json       Routes + function timeout
requirements.txt  Python dependencies
```

## Run locally (UI + API together)

```bash
npm i -g vercel
vercel dev        # open http://localhost:3000
```

Or run just the API with Python:

```bash
python3 -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn api.index:app --reload                       # API at http://localhost:8000
```

## Deploy to Vercel

### Option A: Vercel CLI
```bash
npm i -g vercel
vercel login
vercel            # first run: preview deployment (answer the prompts, accept defaults)
vercel --prod     # production deployment
```

### Option B: GitHub (auto-deploys on every push)
```bash
git init && git add . && git commit -m "SEO AEO GEO audit tool"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```
Then on vercel.com: **Add New → Project → Import** the repo → **Deploy** (no settings to change).

## Notes

- Function timeout is set to 30s in `vercel.json` (Hobby plan allows up to 60s).
- Private, loopback and link-local addresses are blocked (SSRF), including after redirects.
  For local testing against localhost only, set `ALLOW_PRIVATE=1`.
- Audits the initial HTML only (no JavaScript rendering) and does not measure Core Web Vitals.
- Add rate limiting (e.g. Vercel WAF rules or Upstash) before sharing the public URL widely.
