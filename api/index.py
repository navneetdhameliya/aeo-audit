import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from auditor import AuditError, audit  # noqa: E402

app = FastAPI(title="SEO · AEO · GEO Audit")


class AuditRequest(BaseModel):
    url: str


@app.post("/api/audit")
async def run_audit(req: AuditRequest):
    try:
        return await audit(req.url)
    except AuditError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/health")
async def health():
    return {"ok": True}
