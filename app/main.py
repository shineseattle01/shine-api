# app/main.py
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, List

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import Client, create_client
from postgrest.exceptions import APIError
from icalendar import Calendar

# ───────────────────────────────────────────────────────────
# ENV
# ───────────────────────────────────────────────────────────
def need(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v

SUPABASE_URL = need("SUPABASE_URL")
SUPABASE_KEY = need("SUPABASE_SERVICE_ROLE_KEY")
API_KEY_READ = os.getenv("API_KEY_READ")
API_KEY_WRITE = os.getenv("API_KEY_WRITE")
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# force schema public
try:
    supabase.postgrest.schema("public")
except Exception:
    pass

app = FastAPI(title="Shine API", version="1.0.0", docs_url="/docs", redoc_url="/redoc")

# CORS (em produção troque "*" pelo domínio do seu site)
ALLOWED_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],  # precisa aceitar X-API-Key
    expose_headers=["*"],
)

# ───────────────────────────────────────────────────────────
# Auth
# ───────────────────────────────────────────────────────────
def require_key(read_only: bool) -> Callable:
    async def checker(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
        if read_only:
            if x_api_key not in {API_KEY_READ, API_KEY_WRITE}:
                raise HTTPException(status_code=401, detail="Invalid API key (read)")
        else:
            if x_api_key != API_KEY_WRITE:
                raise HTTPException(status_code=401, detail="Invalid API key (write)")
    return checker

# ───────────────────────────────────────────────────────────
# Models
# ───────────────────────────────────────────────────────────
class ICalSourceIn(BaseModel):
    id: str
    ical_url: str
    timezone: Optional[str] = "UTC"
    status: Optional[str] = "active"

# ───────────────────────────────────────────────────────────
# Health
# ───────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"name": "Shine API", "version": "1.0.0"}

@app.get("/health")
def health():
    return {"ok": True}

# ───────────────────────────────────────────────────────────
# Agenda
# ───────────────────────────────────────────────────────────
@app.get("/agenda")
def agenda(
    range_start: datetime = Query(..., description="ISO UTC"),
    range_end: datetime = Query(..., description="ISO UTC"),
    source_id: Optional[str] = Query(None),
    _=Depends(require_key(read_only=True)),
):
    try:
        q = (
            supabase.table("calendar_events")
            .select("id,title,description,location,start,end,source_id,all_day")
            .gte("start", range_start.isoformat())
            .lte("end", range_end.isoformat())
            .order("start")
        )
        if source_id:
            q = q.eq("source_id", source_id)
        resp = q.execute()
        rows = resp.data or []
        return [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "description": r.get("description"),
                "location": r.get("location"),
                "starts_at": r.get("start"),
                "ends_at": r.get("end"),
                "all_day": bool(r.get("all_day", False)),
                "source_id": r.get("source_id"),
            }
            for r in rows
        ]
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))

# ───────────────────────────────────────────────────────────
# KPIs
# ───────────────────────────────────────────────────────────
@app.get("/kpis/daily")
def kpis_daily(day: datetime = Query(...), _=Depends(require_key(read_only=True))):
    try:
        d = day.date().isoformat()
        resp = supabase.table("kpis_daily").select("metric,value,meta").eq("day", d).execute()
        return resp.data or []
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/kpis/summary")
def kpis_summary(
    start: datetime = Query(...),
    end: datetime = Query(...),
    _=Depends(require_key(read_only=True)),
):
    try:
        resp = (
            supabase.table("kpis_daily")
            .select("day,metric,value")
            .gte("day", start.date().isoformat())
            .lte("day", end.date().isoformat())
            .execute()
        )
        agg = defaultdict(float)
        for r in resp.data or []:
            m = r.get("metric"); v = r.get("value")
            if isinstance(v, (int, float)) and m:
                agg[m] += float(v)
        return {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "totals": {
                "events_count": agg.get("events_count", 0.0),
                "hours_scheduled": agg.get("hours_scheduled", 0.0),
            },
        }
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))

# ───────────────────────────────────────────────────────────
# Helpers iCal
# ───────────────────────────────────────────────────────────
def _to_utc(dtobj):
    if isinstance(dtobj, datetime):
        return dtobj.replace(tzinfo=dtobj.tzinfo or timezone.utc).astimezone(timezone.utc)
    start = datetime(dtobj.year, dtobj.month, dtobj.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end

def _rows_from_ics(content: bytes, source_id: str) -> List[dict]:
    cal = Calendar.from_ical(content)
    rows: List[dict] = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        uid = str(comp.get("uid") or "")
        title = str(comp.get("summary") or "")
        description = str(comp.get("description") or "")
        location = str(comp.get("location") or "")
        dtstart = comp.get("dtstart").dt
        dtend = comp.get("dtend").dt if comp.get("dtend") else None

        all_day = False
        if not isinstance(dtstart, datetime):
            all_day = True
            s_utc, e_utc = _to_utc(dtstart)
        else:
            s_utc = _to_utc(dtstart)
            e_utc = _to_utc(dtend) if isinstance(dtend, datetime) else s_utc + timedelta(hours=1)

        rows.append({
            "title": title,
            "description": description,
            "location": location,
            "start": (s_utc if isinstance(s_utc, datetime) else s_utc[0]).isoformat(),
            "end": (e_utc if isinstance(e_utc, datetime) else e_utc[1]).isoformat(),
            "source_id": source_id,
            "uid": uid or None,
            "all_day": all_day,
        })
    return rows

# ───────────────────────────────────────────────────────────
# iCal: fontes (URL) e ingestão
# ───────────────────────────────────────────────────────────
@app.get("/ical/sources")
def list_sources(_=Depends(require_key(read_only=True))):
    try:
        resp = supabase.table("ical_sources").select("*").order("id").execute()
        return resp.data or []
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ical/sources")
def upsert_source(body: ICalSourceIn, _=Depends(require_key(read_only=False))):
    try:
        data = {
            "id": body.id,
            "ical_url": body.ical_url,
            "timezone": body.timezone or "UTC",
            "status": body.status or "active",
        }
        supabase.table("ical_sources").upsert(data, on_conflict="id").execute()
        return {"ok": True, "source": data}
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ical/ingest")
def ical_ingest(_=Depends(require_key(read_only=False))):
    """
    Lê todas as fontes ativas em public.ical_sources e grava eventos em public.calendar_events.
    UNIQUE (source_id, uid, start) evita duplicatas.
    """
    try:
        srcs = supabase.table("ical_sources").select("id,ical_url,status").eq("status", "active").execute().data or []
        total = 0
        for s in srcs:
            r = requests.get(s["ical_url"], timeout=30)
            r.raise_for_status()
            rows = _rows_from_ics(r.content, s["id"])
            if rows:
                supabase.table("calendar_events").upsert(
                    rows, on_conflict="source_id,uid,start", ignore_duplicates=False
                ).execute()
                total += len(rows)
        return {"ingested": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ───────────────────────────────────────────────────────────
# iCal: upload de arquivo .ics
# ───────────────────────────────────────────────────────────
@app.post("/ical/upload")
async def ical_upload(
    file: UploadFile = File(...),
    source_id: str = Form(...),
    timezone_name: str = Form("UTC"),
    _=Depends(require_key(read_only=False)),
):
    try:
        content = await file.read()
        rows = _rows_from_ics(content, source_id)
        if rows:
            supabase.table("calendar_events").upsert(
                rows, on_conflict="source_id,uid,start", ignore_duplicates=False
            ).execute()
        return {"ingested": len(rows), "source_id": source_id}
    except APIError as e:
        raise HTTPException(status_code=500, detail=str(e))

# ───────────────────────────────────────────────────────────
# Reset de teste (opcional)
# ───────────────────────────────────────────────────────────
@app.post("/test/reset")
def test_reset(_=Depends(require_key(read_only=False))):
    if not TEST_MODE:
        raise HTTPException(status_code=403, detail="TEST_MODE desativado")
    supabase.table("calendar_events").delete().in_("source_id", ["manual","google_test","upload","upload-manual"]).execute()
    supabase.table("kpis_daily").delete().gte("day", "2025-01-01").lte("day", "2025-12-31").execute()
    return {"ok": True}
