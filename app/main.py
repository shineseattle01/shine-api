from fastapi import FastAPI, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
from supabase import create_client, Client
import os

app = FastAPI(title="Shine API", version="1.0.0")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
API_KEY_READ = os.environ.get("API_KEY_READ")
API_KEY_WRITE = os.environ.get("API_KEY_WRITE")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --------- Autorização simples por chave no header ---------
def require_key(read_only: bool):
    async def checker(x_api_key: str | None = Header(default=None, alias="X-API-Key")):
        if read_only:
            if x_api_key not in {API_KEY_READ, API_KEY_WRITE}:
                raise HTTPException(status_code=401, detail="Invalid API key (read)")
        else:
            if x_api_key != API_KEY_WRITE:
                raise HTTPException(status_code=401, detail="Invalid API key (write)")
    return checker

@app.get("/health")
async def health():
    return {"ok": True}

# --------- Agenda ---------
class Event(BaseModel):
    id: str
    title: str | None = None
    description: str | None = None
    location: str | None = None
    starts_at: str
    ends_at: str
    all_day: bool = False
    source_id: str | None = None

@app.get("/agenda")
async def agenda(
    range_start: datetime = Query(...),
    range_end: datetime = Query(...),
    source_id: str | None = Query(None),
    _=Depends(require_key(read_only=True)),
):
    query = supabase.table("core.calendar_events") \
        .select("id,title,description,location,starts_at,ends_at,all_day,source_id") \
        .gte("starts_at", range_start.isoformat()) \
        .lte("ends_at", range_end.isoformat()) \
        .order("starts_at")
    if source_id:
        query = query.eq("source_id", source_id)
    resp = query.execute()
    return resp.data or []

# --------- KPIs ---------
@app.get("/kpis/daily")
async def kpis_daily(day: datetime = Query(...), _=Depends(require_key(read_only=True))):
    d = day.date().toordinal()  # só para garantir formato válido
    dstr = day.date().isoformat()
    resp = supabase.table("core.kpis_daily").select("metric,value,meta").eq("day", dstr).execute()
    return resp.data or []

# --------- Ingestão iCal ---------
from app.services.ical_ingest import ingest_all_sources

@app.post("/ical/ingest")
async def ical_ingest(_=Depends(require_key(read_only=False))):
    total = await ingest_all_sources(supabase)
    return {"ingested": total}
