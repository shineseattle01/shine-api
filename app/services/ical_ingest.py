import httpx
from ics import Calendar
from dateutil import tz

async def upsert_event(supabase, ev, source_id: str):
    uid = getattr(ev, 'uid', None) or f"{source_id}:{ev.begin}-{ev.name}"
    title = ev.name
    description = ev.description
    location = ev.location

    starts_at = ev.begin.datetime if hasattr(ev.begin, 'datetime') else ev.begin
    ends_at = ev.end.datetime if hasattr(ev.end, 'datetime') else ev.end
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=tz.gettz('UTC'))
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=tz.gettz('UTC'))

    payload = {
        "source_id": source_id,
        "uid": uid,
        "title": title,
        "description": description,
        "location": location,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "all_day": ev.all_day,
    }
    supabase.table("core.calendar_events").upsert(payload, on_conflict="source_id,uid").execute()

async def ingest_all_sources(supabase) -> int:
    total = 0
    sources = supabase.table("core.ical_sources").select("id,url,timezone").eq("active", True).execute().data or []
    async with httpx.AsyncClient(timeout=20) as client:
        for s in sources:
            r = await client.get(s["url"])
            r.raise_for_status()
            cal = Calendar(r.text)
            for ev in cal.events:
                await upsert_event(supabase, ev, s["id"])
                total += 1
    return total
