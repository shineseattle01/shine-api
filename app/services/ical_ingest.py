# app/services/ical_ingest.py
import httpx
from ics import Calendar
from dateutil import tz
from postgrest.exceptions import APIError

def _to_utc(dt):
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=tz.gettz("UTC"))
    return dt

async def upsert_event(supabase, ev, source_id: str):
    uid = getattr(ev, "uid", None) or f"{source_id}:{ev.begin}-{ev.name}"
    title = ev.name
    description = ev.description
    location = ev.location

    starts_at = ev.begin.datetime if hasattr(ev.begin, "datetime") else ev.begin
    ends_at = ev.end.datetime if hasattr(ev.end, "datetime") else ev.end
    starts_at = _to_utc(starts_at)
    ends_at = _to_utc(ends_at)

    payload = {
        "source_id": source_id,
        "uid": uid,
        "title": title,                 # agora escrevemos em public.calendar_events
        "description": description,
        "location": location,
        "start": starts_at.isoformat(),
        "end": ends_at.isoformat(),
        "all_day": bool(getattr(ev, "all_day", False)),
    }

    # ✅ grava na tabela do schema PUBLIC
    supabase.table("calendar_events").upsert(
        payload,
        on_conflict="source_id,uid,start"   # evita duplicados comuns
    ).execute()

async def ingest_all_sources(supabase) -> int:
    """
    Lê fontes iCal da tabela public.ical_sources (colunas: id, ical_url, timezone, status='active')
    e faz upsert em public.calendar_events.
    """
    total = 0

    try:
        sources_resp = (
            supabase
            .table("ical_sources")
            .select("id,ical_url,timezone,status")
            .eq("status", "active")
            .execute()
        )
    except APIError as e:
        # Se a tabela não existir ainda, interrompe com uma mensagem simples
        raise APIError({"message": "Tabela public.ical_sources não encontrada. Crie-a antes de chamar /ical/ingest."})

    sources = sources_resp.data or []
    if not sources:
        return 0

    async with httpx.AsyncClient(timeout=20) as client:
        for s in sources:
            r = await client.get(s["ical_url"])
            r.raise_for_status()
            cal = Calendar(r.text)
            for ev in cal.events:
                await upsert_event(supabase, ev, s["id"])
                total += 1
    return total
