"""Shared read-only lead semantics for reports, presentation and export."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

REPORT_ZONE = ZoneInfo("America/Sao_Paulo")
REPORT_DAYS = {"daily":1, "weekly":7, "monthly":30}


def lead_status(lead):
    status = str(lead.get("contact_status") or "").lower()
    if status in {"novo", "contactado", "convertido", "descartado", "reposto"}:
        return status
    notes = str(lead.get("notes") or "").strip().lower()
    if notes in {"convertido", "descartado"}:
        return notes
    return "contactado" if lead.get("contacted_at") else "novo"


def received_at(lead):
    for field in ("assigned_at", "created_at", "scraped_at"):
        try:
            value = datetime.fromisoformat(str(lead.get(field) or "").replace("Z", "+00:00"))
            # Historical operator records have no zone; keep their Brazil-local meaning.
            return value.replace(tzinfo=REPORT_ZONE) if value.tzinfo is None else value.astimezone(REPORT_ZONE)
        except (TypeError, ValueError):
            continue
    return None


def select_report_period(leads, period="weekly", now=None):
    if period not in REPORT_DAYS:
        raise ValueError("Escolha Hoje, 7 dias ou 30 dias.")
    now = now or datetime.now(REPORT_ZONE)
    now = now.replace(tzinfo=REPORT_ZONE) if now.tzinfo is None else now.astimezone(REPORT_ZONE)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=REPORT_DAYS[period]-1)
    selected, unknown = [], 0
    for lead in leads:
        value = received_at(lead)
        if value is None:
            unknown += 1
        elif start <= value <= now:
            selected.append(lead)
    return {"leads":selected,"period":period,"days":REPORT_DAYS[period],
            "start_date":start.date().isoformat(),"end_date":now.date().isoformat(),
            "unknown_dates":unknown}


def public_signals(value):
    try:
        items = value if isinstance(value,list) else json.loads(value or "[]")
    except (ValueError,TypeError):
        return []
    if not isinstance(items,list):
        return []
    result=[]
    for item in items:
        text=str(item).strip()
        lower=text.lower()
        if not text or lower.startswith(("blocos", "teto 58", "teto 62", "score usa dor")):
            continue
        if "dor=" in lower and ("cap=" in lower or "vis=" in lower):
            continue
        result.append(text)
    return result


def csv_safe_cell(value):
    if not isinstance(value,str):
        return value
    if value.startswith(("\t","\r","\n")) or value.lstrip().startswith(("=","+","-","@")):
        return "'" + value
    return value
