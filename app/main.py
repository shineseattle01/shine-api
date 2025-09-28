# app/main.py
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from typing import Optional, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from supabase import Client, create_client
from postgrest.exceptions import APIError  # para tratar mensagens do PostgREST


# ------------------------------------------------------------
# Config / bootstrap
# ------------------------------------------------------------
def _getenv(name: str, required: bool = True) -> str:
    val = os.getenv(name)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val or ""

SUPABASE_URL = _getenv("SUPABASE_URL")
SUPABASE_KEY = _getenv("SUPABASE_SERVICE_ROLE_KEY")
API_KEY_READ = os.getenv("API_KEY_READ")    # leitura
API_KEY_WRITE = os.getenv("API_KEY_WRITE")  # escrita

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(
    title="Shine API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ------------------------------------------------------------
# Autorização simples via header X-API-Key
# ------------------------------------------------------------
def require_key(read_only: bool) -> Callable:
    async def checker(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
        if read_only:
            # aceita READ ou WRITE para chamadas de leitura
            if x_api_key not in {API_KEY_READ, API_KEY_WRITE}:
                raise HTTPException(status_code=401, detail="Invalid API key (read)")
        else:
            # somente WRITE para mutações
            if x_api_key != API_KEY_WRITE:
                raise HTTPException(status_code=401, detail="Invalid API key (write)")
    return checker


# ------------------------------------------------------------
# Modelos (se precisar tipar respostas)
# ------------------------------------------------------------
class Event(BaseModel):
    id: str
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    starts_at: str
    ends_at: str
    all_day: bool = False
    source_id: Optional[str] = None


# ------------------------------------------------------------
# Rotas básicas
# ------------------------------------------------------------
@app.get("/")
async def root():
    return {"name": "Shine API", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"ok": True}


# ------------------------------------------------------------
# Helpers de acesso ao schema "core"
# ------------------------------------------------------------
def from_core(table: str):
    """
    Retorna um builder para a tabela no schema 'core'.
    Evita erros do tipo 'Could not find the table public.core.xyz'.
    """
    return supabase.postgrest.schema("core").from_(table)


# ------------------------------------------------------------
# Agenda
# ------------------------------------------------------------
@app.get("/agenda")
async def agenda(
    range_start: datetime = Query(..., description="ISO datetime (UTC)"),
    range_end: datetime = Query(..., description="ISO datetime (UTC)"),
    source_id: Optional[str] = Query(None, description="Filtra por source_id"),
    _=Depends(require_key(read_only=True)),
):
    """
    Lista eventos em `core.calendar_events` dentro do intervalo.
    """
    try:
        pg = from_core("calendar_events")
        query = (
            pg.select("id,title,description,location,starts_at,ends_at,all_day,source_id")
              .gte("starts_at", range_start.isoformat())
              .lte("ends_at", range_end.isoformat())
              .order("starts_at")
        )
        if source_id:
            query = query.eq("source_id", source_id)

        resp = query.execute()
        return resp.data or []
    except APIError as e:
        # Exibe mensagem clara quando tabela/coluna não existe ou outro erro do PostgREST
        raise HTTPException(status_code=500, detail={"postgrest": e.args[0] if e.args else str(e)})


# ------------------------------------------------------------
# KPIs
# ------------------------------------------------------------
@app.get("/kpis/daily")
async def kpis_daily(
    day: datetime = Query(..., description="YYYY-MM-DD ou datetime"),
    _=Depends(require_key(read_only=True)),
):
    """
    Retorna KPIs diários para `day` em `core.kpis_daily`.
    """
    try:
        dstr = day.date().isoformat()
        pg = from_core("kpis_daily")
        resp = pg.select("metric,value,meta").eq("day", dstr).execute()
        return resp.data or []
    except APIError as e:
        raise HTTPException(status_code=500, detail={"postgrest": e.args[0] if e.args else str(e)})


@app.get("/kpis/summary")
async def kpis_summary(
    start: datetime = Query(..., description="Início (YYYY-MM-DD ou datetime)"),
    end: datetime = Query(..., description="Fim (YYYY-MM-DD ou datetime)"),
    _=Depends(require_key(read_only=True)),
):
    """
    Agrega KPIs diários no intervalo [start, end] somando por 'metric'.
    Espera linhas no formato: (day, metric, value).
    """
    try:
        pg = from_core("kpis_daily")
        resp = (
            pg.select("day,metric,value")
              .gte("day", start.date().isoformat())
              .lte("day", end.date().isoformat())
              .execute()
        )
        rows = resp.data or []

        agg = defaultdict(float)
        for r in rows:
            m = r.get("metric")
            v = r.get("value")
            if m is not None and isinstance(v, (int, float)):
                agg[m] += float(v)

        return {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "totals": {
                "events_count": agg.get("events_count", 0.0),
                "hours_scheduled": agg.get("hours_scheduled", 0.0),
                # adicione outras métricas se existirem
            },
        }
    except APIError as e:
        raise HTTPException(status_code=500, detail={"postgrest": e.args[0] if e.args else str(e)})


# ------------------------------------------------------------
# Ingestão iCal
# ------------------------------------------------------------
from app.services.ical_ingest import ingest_all_sources  # mantém teu path

@app.post("/ical/ingest")
async def ical_ingest(_=Depends(require_key(read_only=False))):
    """
    Dispara a ingestão de todas as fontes iCal configuradas.
    Requer X-API-Key de escrita.
    """
    try:
        total = await ingest_all_sources(supabase)
        return {"ingested": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
