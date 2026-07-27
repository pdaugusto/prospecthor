"""
Garante sobras: leads livres (assigned_to NULL) com contato ficam no pool
do Patrão — scoreia o que faltar, NÃO atribui a ninguém.

Uso:
  python scripts/_harvest_sobras.py
  python scripts/_harvest_sobras.py --niche fisioterapia
  python scripts/_harvest_sobras.py --since 2026-07-21
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv(ROOT / ".env")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", default="", help="filtra nicho (ex: fisioterapia)")
    ap.add_argument("--since", default="", help="created_at >= (ISO parcial)")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    where = [
        "assigned_to IS NULL",
        "(business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')",
        """(
            lead_class = 'raio'
            OR website_status IN ('sem_site', 'so_social')
            OR website IS NULL
            OR TRIM(COALESCE(website, '')) = ''
        )""",
    ]
    params: list = []
    if args.niche:
        where.append("niche = %s")
        params.append(args.niche.strip().lower())
    if args.since:
        where.append("created_at::text >= %s")
        params.append(args.since)

    sql = f"""
        SELECT id, name, city, niche, phone, instagram_url, instagram_username,
               lead_score, lead_class, website_status, source, created_at, scored_at
        FROM companies
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT %s
    """
    params.append(int(args.limit))
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    print(f"Sobras candidatas: {len(rows)}")

    from src.users import lead_has_client_contact, has_contact_phone
    from src.scorer import LeadScorer

    scorer = LeadScorer()
    scored = 0
    ok_contact = 0
    no_contact = 0
    for r in rows:
        has_c = lead_has_client_contact(r) or has_contact_phone(r.get("phone"))
        if not has_c:
            no_contact += 1
            continue
        ok_contact += 1
        if not r.get("scored_at") or not r.get("lead_score"):
            try:
                # NUNCA assign — são sobras
                scorer.score_one(int(r["id"]), assign=False)
                scored += 1
                print(
                    f"  score sobra id={r['id']} {r.get('name')!r} · "
                    f"{r.get('city')} · {r.get('niche')}"
                )
            except Exception as exc:
                print(f"  score fail id={r['id']}: {exc}")
        else:
            print(
                f"  ok sobra id={r['id']} score={r.get('lead_score')} "
                f"{r.get('name')!r} · {r.get('niche')}/{r.get('city')}"
            )

    # resumo por nicho
    cur.execute(
        """
        SELECT niche, COUNT(*) AS n
        FROM companies
        WHERE assigned_to IS NULL
          AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
          AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
          )
        GROUP BY niche
        ORDER BY n DESC
        LIMIT 20
        """
    )
    print("\n=== Pool sobras por nicho ===")
    for r in cur.fetchall():
        print(f"  {r['niche'] or '?'}: {r['n']}")

    print(
        f"\nDone. com_contato={ok_contact} sem_contato={no_contact} "
        f"scoreados_agora={scored} (nenhum assign — ficam livres)"
    )
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
