"""Completa +1 lead faltante para Blak, Bruno e Marcos se houver sobra livre."""
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

TARGETS = ("blak", "bruno", "marcos")
# meta esperada por missão (padrão cockpit)
DEFAULT_META = 20


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """
        SELECT id, username, label, monthly_quota, active
        FROM app_users
        WHERE lower(username) = ANY(%s)
           OR lower(COALESCE(label,'')) = ANY(%s)
        ORDER BY id
        """,
        (list(TARGETS), list(TARGETS)),
    )
    users = [dict(r) for r in cur.fetchall()]
    print("Users found:", [(u["id"], u["username"], u.get("label")) for u in users])

    # also fuzzy
    if len(users) < 3:
        cur.execute(
            """
            SELECT id, username, label FROM app_users
            WHERE lower(username) LIKE ANY(%s)
               OR lower(COALESCE(label,'')) LIKE ANY(%s)
            ORDER BY id
            """,
            (
                [f"%{t}%" for t in TARGETS],
                [f"%{t}%" for t in TARGETS],
            ),
        )
        users = [dict(r) for r in cur.fetchall()]
        print("Fuzzy users:", [(u["id"], u["username"], u.get("label")) for u in users])

    # dedupe by id
    seen = set()
    uniq = []
    for u in users:
        if u["id"] not in seen:
            seen.add(u["id"])
            uniq.append(u)
    users = uniq

    for u in users:
        uid = int(u["id"])
        name = u.get("label") or u.get("username")
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM companies
            WHERE assigned_to = %s
            """,
            (uid,),
        )
        total = int(cur.fetchone()["n"])
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM companies
            WHERE assigned_to = %s
              AND (lead_class = 'raio' OR website_status IN ('sem_site','so_social')
                   OR website IS NULL OR TRIM(COALESCE(website,'')) = '')
            """,
            (uid,),
        )
        raios = int(cur.fetchone()["n"])
        print(f"\n=== {name} (id={uid}) total={total} raios≈{raios} ===")

        # recent niches/cities they have
        cur.execute(
            """
            SELECT niche, city, COUNT(*) AS n
            FROM companies WHERE assigned_to = %s
            GROUP BY niche, city
            ORDER BY n DESC
            LIMIT 15
            """,
            (uid,),
        )
        for r in cur.fetchall():
            print(f"  {r['niche']}/{r['city']}: {r['n']}")

        need = max(0, DEFAULT_META - total)
        # if they have more than 20 from multiple missions, still check if last mission short
        # user said faltaram 1 → need 1 if total % something; usually want +1 if under 20
        # if total is 19, 39, etc. add 1 to next multiple of 20? Simpler: if total < 20 need 20-total
        # if total is 19 for first mission: need 1
        # if total is 39: need 1 for second? 
        if total >= DEFAULT_META and total % DEFAULT_META != DEFAULT_META - 1 and total % DEFAULT_META != 0:
            # e.g. 18 → need 2 to 20; 19 → need 1
            pass
        if total % DEFAULT_META == 0 and total > 0:
            print(f"  já em múltiplo de {DEFAULT_META} ({total}) — nada a fazer por padrão")
            # still if they said missing 1, maybe need total+1 for unfinished 20
            need = 0
        elif total % DEFAULT_META == DEFAULT_META - 1:
            need = 1
            print(f"  clássico {total} → falta 1 para fechar bloco de {DEFAULT_META}")
        else:
            rem = DEFAULT_META - (total % DEFAULT_META)
            need = rem if rem < DEFAULT_META else 0
            print(f"  total {total}, falta {need} para próximo múltiplo de {DEFAULT_META}")

        if need <= 0:
            continue

        # niches they already have
        cur.execute(
            "SELECT DISTINCT niche FROM companies WHERE assigned_to = %s AND niche IS NOT NULL",
            (uid,),
        )
        niches = [r["niche"] for r in cur.fetchall() if r["niche"]]
        cur.execute(
            "SELECT DISTINCT city FROM companies WHERE assigned_to = %s AND city IS NOT NULL",
            (uid,),
        )
        cities = [r["city"] for r in cur.fetchall() if r["city"]]

        # free leads matching their niches/cities, with contact, high score first
        cur.execute(
            """
            SELECT id, name, phone, city, niche, source, lead_score, website, website_status,
                   instagram_url, instagram_username
            FROM companies
            WHERE assigned_to IS NULL
              AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
              AND (
                    lead_class = 'raio'
                    OR website_status IN ('sem_site', 'so_social')
                    OR website IS NULL
                    OR TRIM(COALESCE(website, '')) = ''
              )
              AND (
                    %s = 0 OR niche = ANY(%s)
              )
            ORDER BY
              CASE WHEN city = ANY(%s) THEN 0 ELSE 1 END,
              lead_score DESC NULLS LAST,
              id DESC
            LIMIT 30
            """,
            (0 if not niches else 1, niches or [""], cities or [""]),
        )
        free = [dict(r) for r in cur.fetchall()]
        print(f"  sobras candidatas: {len(free)}")

        assigned_n = 0
        for lead in free:
            if assigned_n >= need:
                break
            if not lead_has_client_contact(lead) and not has_contact_phone(lead.get("phone")):
                print(f"  skip sem contato id={lead['id']} {lead.get('name')}")
                continue
            lid = int(lead["id"])
            ok = manual_assign(lid, uid)
            if ok:
                assigned_n += 1
                print(
                    f"  ✓ ATRIBUÍDO id={lid} score={lead.get('lead_score')} "
                    f"{lead.get('name')!r} · {lead.get('city')} → {name}"
                )
            else:
                print(f"  ✗ falhou assign id={lid} {lead.get('name')}")

        cur.execute("SELECT COUNT(*) AS n FROM companies WHERE assigned_to = %s", (uid,))
        new_total = int(cur.fetchone()["n"])
        print(f"  resultado: {total} → {new_total} ( +{new_total - total} )")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
