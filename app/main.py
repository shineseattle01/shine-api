# app/main.py
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime
from typing import Optional, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import Client, create_client
from postgrest.exceptions import APIError  # erros vindos do PostgREST

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
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# cria cliente supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ✅ força o PostgREST a usar o schema 'public' como padrão
try:
    supabase.postgrest.schema("public")
except Exception:
    # algumas versões não expõem .postgrest; se falhar, seguimos com o default (já é 'public')
    pass

app = FastAPI(
    title="Shine API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# --- CORS: permita o site chamar a API ---
ALLOWED_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,   # ex.: "http://localhost:3000,https://seusite.vercel.app"
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],             # aceitar header X-API-Key
    expose_headers=["*"],
)

# ------------------------------------------------------------
# Autorização simples via header X-API-Key
# ------------------------------------------------------------
def require_key(read_only: bool) -> Callable:
    async def checker(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
        if read_only:
            if x_api_key not in {API_KEY_READ, API_KEY_WRITE}:
                raise HTTPException(status_code=401, detail="Invalid API key (read)")
        else:
            if x_api_key != API_KEY_WRITE:
                raise HTTPException(status_code=401, detail="Invalid API key (write)")
    return checker

# ------------------------------------------------------------
# Modelos (se quiser tipar respostas)
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
# Agenda (lê eventos do schema PUBLIC)
# ------------------------------------------------------------
@app.get("/agenda")
async def agenda(
    range_start: datetime = Query(..., description="ISO datetime (UTC)"),
    range_end: datetime = Query(..., description="ISO datetime (UTC)"),
    source_id: Optional[str] = Query(None, description="Filtra por source_id"),
    _=Depends(require_key(read_only=True)),
):
    """
    Lista eventos na tabela public.calendar_events dentro do intervalo.
    Espera colunas: id, title, description, location, start, end, source_id, (opcional) all_day.
    """
    try:
        query = (
            supabase
            .table("calendar_events")  # public por padrão
            .select("id,title,description,location,start,end,source_id,all_day")
            .gte("start", range_start.isoformat())
            .lte("end", range_end.isoformat())
            .order("start")
        )
        if source_id:
            query = query.eq("source_id", source_id)

        resp = query.execute()
        data = resp.data or []

        normalized = []
        for r in data:
            normalized.append({
                "id": r.get("id"),
                "title": r.get("title"),
                "description": r.get("description"),
                "location": r.get("location"),
                "starts_at": r.get("start"),
                "ends_at": r.get("end"),
                "all_day": bool(r.get("all_day", False)),
                "source_id": r.get("source_id"),
            })
        return normalized

    except APIError as e:
        raise HTTPException(status_code=500, detail={"postgrest": e.args[0] if e.args else str(e)})

# ------------------------------------------------------------
# KPIs (tabelas no schema PUBLIC)
# ------------------------------------------------------------
@app.get("/kpis/daily")
async def kpis_daily(
    day: datetime = Query(..., description="YYYY-MM-DD ou datetime"),
    _=Depends(require_key(read_only=True)),
):
    """
    Retorna KPIs diários para `day` na tabela public.kpis_daily.
    """
    try:
        dstr = day.date().isoformat()
        resp = (
            supabase.table("kpis_daily")  # public por padrão
            .select("metric,value,meta")
            .eq("day", dstr)
            .execute()
        )
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
        resp = (
            supabase.table("kpis_daily")  # public por padrão
            .select("day,metric,value")
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
            },
        }
    except APIError as e:
        raise HTTPException(status_code=500, detail={"postgrest": e.args[0] if e.args else str(e)})

# ------------------------------------------------------------
# Ingestão iCal (usa services/ical_ingest.py) — schema PUBLIC
# ------------------------------------------------------------
from app.services.ical_ingest import ingest_all_sources

@app.post("/ical/ingest")
async def ical_ingest(_=Depends(require_key(read_only=False))):
    """
    Dispara a ingestão de todas as fontes iCal configuradas (public.ical_sources).
    Requer X-API-Key de escrita.
    """
    try:
        total = await ingest_all_sources(supabase)
        return {"ingested": total}
    except APIError as e:
        raise HTTPException(status_code=500, detail={"postgrest": e.args[0] if e.args else str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------------------------
# Rota auxiliar de TESTE (reset rápido) — opcional
# ------------------------------------------------------------
@app.post("/test/reset")
async def test_reset(_=Depends(require_key(read_only=False))):
    """
    Limpa dados de teste. Só funciona quando TEST_MODE=true.
    """
    if not TEST_MODE:
        raise HTTPException(status_code=403, detail="TEST_MODE desativado")

    try:
        # limpa eventos de fontes de teste
        supabase.table("calendar_events").delete().in_("source_id", ["manual", "google_test"]).execute()
        # limpa KPIs de 2025 (ajuste conforme necessidade)
        supabase.table("kpis_daily").delete().gte("day", "2025-01-01").lte("day", "2025-12-31").execute()
        return {"ok": True}
    except APIError as e:
        raise HTTPException(status_code=500, detail={"postgrest": e.args[0] if e.args else str(e)})
