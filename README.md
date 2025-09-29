# Shine API

API em **FastAPI** para servir **Agenda** (eventos iCal armazenados no Supabase) e **KPIs** para o dashboard Next.js.

## Endpoints

- `GET /health` – prova de vida.
- `GET /agenda?range_start=YYYY-MM-DDTHH:MM:SSZ&range_end=...&source_id=opcional`  
  Retorna eventos de `core.calendar_events`.
- `GET /kpis/daily?day=YYYY-MM-DD`  
  Retorna métricas em `core.kpis_daily`.
- `POST /ical/ingest` – baixa iCals listados em `core.ical_sources` e upserta eventos.

> **Auth**: use header `X-API-Key`.  
> Leitura: `API_KEY_READ` **ou** `API_KEY_WRITE`.  
> Escrita: apenas `API_KEY_WRITE`.

## Variáveis de ambiente

- `SUPABASE_URL` – URL do seu projeto Supabase.
- `SUPABASE_SERVICE_ROLE_KEY` – **Service Role** (chave secreta; use apenas no backend).
- `API_KEY_READ` – key de leitura.
- `API_KEY_WRITE` – key de escrita.
- `TZ` – ex.: `America/Sao_Paulo` (opcional, só para logging/scheduler).

## Tabelas esperadas (Supabase)

- `core.ical_sources(id, url, timezone, active bool)`  
- `core.calendar_events(id, source_id, uid, title, description, location, starts_at, ends_at, all_day)`  
  *unique* em `(source_id, uid)`.
- `core.kpis_daily(day date, metric text, value numeric, meta jsonb)`

## Rodando localmente

```bash
python -m venv .venv && .venv\Scripts\activate  # Windows
pip install -r requirements.txt
set SUPABASE_URL=...
set SUPABASE_SERVICE_ROLE_KEY=...
set API_KEY_READ=...
set API_KEY_WRITE=...
uvicorn app.main:app --reload
