"""Espera missão Matheus (8b6129d3) terminar e colhe sobras livres."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

MID = "8b6129d3"
NICHES = [
    "advocacia",
    "clinica_medica",
    "fisioterapia",
    "hotel",
    "joalheria",
    "seguradora",
]


def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    state_p = ROOT / "data" / "cockpit_state.json"
    miss_p = ROOT / "data" / "missions.json"

    for i in range(240):  # ~60 min
        st = _load_json(state_p)
        ms = _load_json(miss_p)
        m = next((x for x in (ms.get("missions") or []) if x.get("id") == MID), None)
        status = st.get("status")
        sess = st.get("session_leads")
        tgt = st.get("mission_target")
        mstat = (m or {}).get("status")
        msg = str(st.get("message") or "")[:90]
        print(
            f"tick={i} cockpit={status} meta={sess}/{tgt} mission={mstat} | {msg}",
            flush=True,
        )
        done = False
        if mstat in ("ok", "erro", "cancelada", "done"):
            done = True
        if status == "parado" and mstat and mstat != "rodando":
            done = True
        if status == "parado" and (not m or mstat != "rodando"):
            done = True
        if done:
            print(
                "DONE",
                mstat,
                "leads_found=",
                (m or {}).get("leads_found"),
                flush=True,
            )
            break
        time.sleep(15)
    else:
        print("TIMEOUT", flush=True)

    # harvest sobras of mission niches (no assign)
    import psycopg2
    import psycopg2.extras
    from src.users import lead_has_client_contact, has_contact_phone
    from src.scorer import LeadScorer

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id, name, city, niche, phone, instagram_url, instagram_username,
               lead_score, lead_class, website_status, source, scored_at, assigned_to
        FROM companies
        WHERE assigned_to IS NULL
          AND niche = ANY(%s)
          AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
          AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
          )
          AND created_at::text >= %s
        ORDER BY id DESC
        LIMIT 300
        """,
        (NICHES, "2026-07-21"),
    )
    rows = [dict(r) for r in cur.fetchall()]
    print(f"sobras livres nos nichos da missão: {len(rows)}", flush=True)

    scorer = LeadScorer()
    scored = 0
    kept = 0
    for r in rows:
        if not (lead_has_client_contact(r) or has_contact_phone(r.get("phone"))):
            continue
        kept += 1
        if not r.get("scored_at") or not r.get("lead_score"):
            try:
                scorer.score_one(int(r["id"]), assign=False)
                scored += 1
                print(
                    f"  score sobra id={r['id']} {r.get('name')!r} "
                    f"{r.get('niche')}/{r.get('city')}",
                    flush=True,
                )
            except Exception as exc:
                print(f"  fail id={r['id']}: {exc}", flush=True)
        else:
            print(
                f"  ok sobra id={r['id']} score={r.get('lead_score')} "
                f"{r.get('name')!r} {r.get('niche')}",
                flush=True,
            )

    # Matheus count
    cur.execute(
        "SELECT COUNT(*) AS n FROM companies WHERE assigned_to = 17"
    )
    print(f"Matheus (id=17) total assigned: {cur.fetchone()['n']}", flush=True)

    cur.execute(
        """
        SELECT niche, COUNT(*) n FROM companies
        WHERE assigned_to IS NULL AND niche = ANY(%s)
        GROUP BY niche ORDER BY n DESC
        """,
        (NICHES,),
    )
    print("pool sobras por nicho (missão):", flush=True)
    for r in cur.fetchall():
        print(f"  {r['niche']}: {r['n']}", flush=True)

    print(f"harvest done kept={kept} scored_now={scored}", flush=True)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
