import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from auditor import AuditError, audit  # noqa: E402

app = FastAPI(title="SEO · AEO · GEO Audit")


class AuditRequest(BaseModel):
    url: str


# Vercel may pass the function a rewritten path (e.g. /api/index) instead of
# /api/audit, so these handlers accept any path rather than one fixed route.
@app.post("/{full_path:path}")
async def run_audit(full_path: str, req: AuditRequest):
    try:
        return await audit(req.url)
    except AuditError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/{full_path:path}")
async def health(full_path: str):
    return {"ok": True, "path": "/" + full_path}