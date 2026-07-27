"""Diagnóstico Lima: contagem assigned vs meta 20."""
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


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """
        SELECT id, username, label, monthly_quota, active
        FROM app_users
        WHERE lower(username) LIKE %s OR lower(COALESCE(label,'')) LIKE %s
        ORDER BY id
        """,
        ("%lima%", "%lima%"),
    )
    users = [dict(r) for r in cur.fetchall()]
    print("USERS:", users)

    for u in users:
        uid = int(u["id"])
        cur.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE assigned_to = %s",
            (uid,),
        )
        total = int(cur.fetchone()["n"])
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM companies
            WHERE assigned_to = %s
              AND (
                    lead_class = 'raio'
                    OR website_status IN ('sem_site', 'so_social')
                    OR website IS NULL
                    OR TRIM(COALESCE(website, '')) = ''
              )
            """,
            (uid,),
        )
        raios = int(cur.fetchone()["n"])
        print(f"\n=== {u.get('username')} id={uid} total={total} raios≈{raios} ===")

        cur.execute(
            """
            SELECT niche, city, COUNT(*) AS n
            FROM companies WHERE assigned_to = %s
            GROUP BY niche, city
            ORDER BY n DESC
            """,
            (uid,),
        )
        for r in cur.fetchall():
            print(f"  {r['niche']}/{r['city']}: {r['n']}")

        cur.execute(
            """
            SELECT id, name, city, niche, lead_score, lead_class, source,
                   website_status, phone, assigned_at, created_at
            FROM companies
            WHERE assigned_to = %s
            ORDER BY COALESCE(assigned_at, created_at) DESC NULLS LAST, id DESC
            LIMIT 25
            """,
            (uid,),
        )
        print("--- recent ---")
        for r in cur.fetchall():
            print(
                f"  id={r['id']} score={r.get('lead_score')} "
                f"{r.get('name')!r} · {r.get('city')} · {r.get('source')} · "
                f"cls={r.get('lead_class')} ws={r.get('website_status')}"
            )

        # leads da missão MG restaurante sem assign (órfãos da sessão?)
        cur.execute(
            """
            SELECT id, name, city, niche, source, lead_score, assigned_to, created_at
            FROM companies
            WHERE niche = 'restaurante'
              AND (
                    city ILIKE %s OR city ILIKE %s OR city ILIKE %s
                    OR city ILIKE %s OR city ILIKE %s OR city ILIKE %s
              )
              AND created_at >= %s
            ORDER BY id DESC
            LIMIT 40
            """,
            (
                "%Belo Horizonte%",
                "%Juiz de Fora%",
                "%Uberlândia%",
                "%Uberlandia%",
                "%BH%",
                "%Juiz%",
                "2026-07-21T22:00:00",
            ),
        )
        print("\n--- restaurante MG recent (qualquer assign) ---")
        for r in cur.fetchall():
            print(
                f"  id={r['id']} assign={r.get('assigned_to')} "
                f"{r.get('name')!r} · {r.get('city')} · {r.get('source')}"
            )

    # free pool usable
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM companies
        WHERE assigned_to IS NULL
          AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
          AND (
                lead_class = 'raio'
                OR website_status IN ('sem_site', 'so_social')
                OR website IS NULL
                OR TRIM(COALESCE(website, '')) = ''
          )
        """
    )
    print("\nfree raios-ish:", cur.fetchone()["n"])

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
