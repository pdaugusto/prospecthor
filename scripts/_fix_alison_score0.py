"""Troca lead score 0 da Alison por sobra melhor de odontologia."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv(ROOT / ".env")
from src.users import manual_assign, lead_has_client_contact, has_contact_phone

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

UID = 14  # Alison

cur.execute(
    """
    SELECT id, name, city, lead_score FROM companies
    WHERE assigned_to = %s AND (lead_score IS NULL OR lead_score < 50)
    ORDER BY lead_score ASC NULLS FIRST, id
    """,
    (UID,),
)
bad = [dict(r) for r in cur.fetchall()]
print("Alison baixos:", bad)

cur.execute(
    """
    SELECT id, name, city, niche, lead_score, phone, website, website_status,
           instagram_url, instagram_username
    FROM companies
    WHERE assigned_to IS NULL
      AND niche = 'odontologia'
      AND (lead_score IS NULL OR lead_score >= 60)
      AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
    ORDER BY lead_score DESC NULLS LAST
    LIMIT 15
    """
)
free = [dict(r) for r in cur.fetchall()]
print(f"sobras odonto boas: {len(free)}")
for r in free[:8]:
    print(f"  {r['lead_score']} {r['name'][:40]} · {r['city']}")

if bad and free:
    b = bad[0]
    # pick first with contact
    good = None
    for f in free:
        if lead_has_client_contact(f) or has_contact_phone(f.get("phone")):
            good = f
            break
    if good:
        manual_assign(int(b["id"]), None)
        ok = manual_assign(int(good["id"]), UID)
        print(
            f"Troca: devolveu id={b['id']} score={b.get('lead_score')} "
            f"→ Alison id={good['id']} score={good.get('lead_score')} {good.get('name')!r}"
        )
        print("ok=", ok)
    else:
        print("Nenhuma sobra com contato")
elif not free:
    print("Sem sobra odonto boa — mantém o score 0 ou rescore")
    if bad:
        from src.scorer import LeadScorer
        s = LeadScorer()
        for b in bad:
            r = s.score_one(int(b["id"]))
            print(f"rescore {b['id']}: {b.get('lead_score')} → {r.get('lead_score') if r else '?'}")

cur.execute(
    "SELECT id, name, lead_score, city FROM companies WHERE assigned_to=%s ORDER BY lead_score ASC NULLS FIRST",
    (UID,),
)
print("\nAlison final (20?):")
rows = cur.fetchall()
print("total", len(rows))
for r in rows[:3]:
    print("  low", r["lead_score"], r["name"][:40])
for r in rows[-3:]:
    print("  high", r["lead_score"], r["name"][:40])

cur.close()
conn.close()
