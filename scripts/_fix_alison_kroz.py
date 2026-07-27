"""Alison: +1 se 19. Kroz: deixa 20, sobras (score baixo primeiro) viram livres."""
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

META = 20


def find_users(cur):
    cur.execute(
        """
        SELECT id, username, label FROM app_users
        WHERE lower(username) LIKE ANY(%s)
           OR lower(COALESCE(label,'')) LIKE ANY(%s)
        ORDER BY id
        """,
        (
            ["%alison%", "%kroz%", "%croz%"],
            ["%alison%", "%kroz%", "%croz%"],
        ),
    )
    return [dict(r) for r in cur.fetchall()]


def count_assigned(cur, uid: int) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM companies WHERE assigned_to = %s", (uid,))
    return int(cur.fetchone()["n"])


def list_leads(cur, uid: int):
    cur.execute(
        """
        SELECT id, name, city, niche, source, lead_score, phone
        FROM companies WHERE assigned_to = %s
        ORDER BY lead_score ASC NULLS FIRST, id ASC
        """,
        (uid,),
    )
    return [dict(r) for r in cur.fetchall()]


def free_candidates(cur, niches, cities, limit=40):
    cur.execute(
        """
        SELECT id, name, city, niche, source, lead_score, phone, website,
               website_status, instagram_url, instagram_username
        FROM companies
        WHERE assigned_to IS NULL
          AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
          AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
          )
          AND (%s = 0 OR niche = ANY(%s))
        ORDER BY
          CASE WHEN city = ANY(%s) THEN 0 ELSE 1 END,
          lead_score ASC NULLS FIRST,
          id DESC
        LIMIT %s
        """,
        (0 if not niches else 1, niches or [""], cities or [""], limit),
    )
    return [dict(r) for r in cur.fetchall()]


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    users = find_users(cur)
    print("Users:", [(u["id"], u["username"], u.get("label")) for u in users])

    alison = next(
        (u for u in users if "alison" in (u.get("label") or "").lower()
         or "alison" in (u.get("username") or "").lower()),
        None,
    )
    kroz = next(
        (u for u in users if "kroz" in (u.get("label") or "").lower()
         or "kroz" in (u.get("username") or "").lower()
         or "croz" in (u.get("label") or "").lower()),
        None,
    )

    # --- ALISON: completar até 20 ---
    if alison:
        uid = int(alison["id"])
        name = alison.get("label") or alison["username"]
        total = count_assigned(cur, uid)
        print(f"\n=== ALISON {name} id={uid} total={total} ===")
        leads = list_leads(cur, uid)
        niches = list({r["niche"] for r in leads if r.get("niche")})
        cities = list({r["city"] for r in leads if r.get("city")})
        for r in leads[:5]:
            print(f"  sample low: {r['lead_score']} {r['name'][:40]} {r['city']}")
        print(f"  niches={niches} cities={cities}")

        need = max(0, META - total)
        if need:
            free = free_candidates(cur, niches, cities, limit=40)
            # prefer same niche; low score free is fine for fill
            free.sort(
                key=lambda x: (
                    0 if x.get("city") in cities else 1,
                    0 if x.get("niche") in niches else 1,
                    -(x.get("lead_score") or 0),  # higher score better for the missing one
                    -int(x["id"]),
                )
            )
            print(f"  need={need} free={len(free)}")
            added = 0
            for lead in free:
                if added >= need:
                    break
                if not lead_has_client_contact(lead) and not has_contact_phone(lead.get("phone")):
                    continue
                ok = manual_assign(int(lead["id"]), uid)
                if ok:
                    added += 1
                    print(
                        f"  ✓ Alison +1 id={lead['id']} score={lead.get('lead_score')} "
                        f"{lead.get('name')!r} · {lead.get('city')}"
                    )
            print(f"  Alison agora: {count_assigned(cur, uid)}")
        else:
            print("  Alison já tem >= 20")
    else:
        print("Alison não encontrada")

    # --- KROZ: se > 20, devolver sobras (score baixo primeiro) ---
    if kroz:
        uid = int(kroz["id"])
        name = kroz.get("label") or kroz["username"]
        total = count_assigned(cur, uid)
        print(f"\n=== KROZ {name} id={uid} total={total} ===")
        leads = list_leads(cur, uid)  # already score ASC
        print(f"  lowest scores:")
        for r in leads[:8]:
            print(f"    {r['lead_score']} id={r['id']} {r['name'][:40]} · {r['city']}")

        if total > META:
            extra = total - META
            print(f"  sobras a devolver: {extra} (prioriza score baixo)")
            # lowest score first already
            to_free = leads[:extra]
            for lead in to_free:
                lid = int(lead["id"])
                ok = manual_assign(lid, None)  # livre
                if ok:
                    print(
                        f"  → SOBRA id={lid} score={lead.get('lead_score')} "
                        f"{lead.get('name')!r} · {lead.get('city')}"
                    )
                else:
                    # force SQL if manual_assign blocks null somehow
                    cur.execute(
                        "UPDATE companies SET assigned_to = NULL, assigned_at = NULL WHERE id = %s",
                        (lid,),
                    )
                    conn.commit()
                    print(f"  → SOBRA (sql) id={lid} score={lead.get('lead_score')} {lead.get('name')!r}")
            print(f"  Kroz agora: {count_assigned(cur, uid)}")
        elif total < META:
            need = META - total
            print(f"  Kroz faltam {need} — completando")
            niches = list({r["niche"] for r in leads if r.get("niche")})
            cities = list({r["city"] for r in leads if r.get("city")})
            free = free_candidates(cur, niches, cities, limit=40)
            free.sort(
                key=lambda x: (
                    0 if x.get("city") in cities else 1,
                    0 if x.get("niche") in niches else 1,
                    -(x.get("lead_score") or 0),
                )
            )
            added = 0
            for lead in free:
                if added >= need:
                    break
                if not lead_has_client_contact(lead) and not has_contact_phone(lead.get("phone")):
                    continue
                if manual_assign(int(lead["id"]), uid):
                    added += 1
                    print(f"  ✓ Kroz +1 id={lead['id']} {lead.get('name')!r}")
            print(f"  Kroz agora: {count_assigned(cur, uid)}")
        else:
            print("  Kroz já tem exatamente 20")
    else:
        print("Kroz não encontrado")

    # summary
    print("\n=== RESUMO ===")
    for u in users:
        n = count_assigned(cur, int(u["id"]))
        print(f"  {u.get('label') or u['username']}: {n}")

    # count free pool
    cur.execute("SELECT COUNT(*) AS n FROM companies WHERE assigned_to IS NULL")
    print(f"  Pool livre (sobras): {cur.fetchone()['n']}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
