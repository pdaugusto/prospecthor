"""Rescore leads do Blak (Fonte B / OSM / CNPJ) e lista resultados."""
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

from src.scorer import LeadScorer


def main() -> None:
    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        print("DATABASE_URL ausente")
        sys.exit(1)

    conn = psycopg2.connect(url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """
        SELECT id, username, label FROM app_users
        WHERE lower(username) LIKE %s OR lower(COALESCE(label,'')) LIKE %s
        """,
        ("%blak%", "%blak%"),
    )
    users = [dict(r) for r in cur.fetchall()]
    print("users:", users)

    if not users:
        cur.execute("SELECT id, username, label FROM app_users ORDER BY id")
        for u in cur.fetchall():
            print(" ", dict(u))
        sys.exit(1)

    uid = int(users[0]["id"])
    uname = users[0].get("username")

    # leads do Blak + fonte B/osm/cnpj recentes sem dono ou dele
    cur.execute(
        """
        SELECT id, name, source, niche, city, phone, lead_score, lead_class,
               website_status, instagram_url, rating, review_count, assigned_to, scored_at
        FROM companies
        WHERE assigned_to = %s
           OR (source ILIKE ANY(ARRAY['%%osm%%','%%cnpj%%','%%fonte%%'])
               AND (assigned_to = %s OR assigned_to IS NULL)
               AND (niche = 'restaurante' OR niche ILIKE '%%rest%%')
               AND city ILIKE ANY(ARRAY['%%Vila Velha%%','%%Vitória%%','%%Vitoria%%','%%Serra%%',
                                        '%%Cariacica%%','%%Guarapari%%','%%Cachoeiro%%','%%Linhares%%','%%Colatina%%'])
              )
        ORDER BY id DESC
        LIMIT 80
        """,
        (uid, uid),
    )
    rows = [dict(r) for r in cur.fetchall()]
    print(f"candidatos: {len(rows)} (user_id={uid} {uname})")

    # se poucos, pega todos assigned to blak + todos source osm/cnpj recentes
    if len(rows) < 8:
        cur.execute(
            """
            SELECT id, name, source, niche, city, phone, lead_score, lead_class,
                   website_status, instagram_url, rating, review_count, assigned_to, scored_at
            FROM companies
            WHERE assigned_to = %s
               OR lower(COALESCE(source,'')) IN ('osm','cnpj','cnpj+osm','fonte_b','fonte-b')
            ORDER BY id DESC
            LIMIT 100
            """,
            (uid,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        print(f"candidatos ampliado: {len(rows)}")

    for r in rows[:20]:
        print(
            f"  id={r['id']} score={r.get('lead_score')} src={r.get('source')} "
            f"{r.get('name')!r} {r.get('city')} phone={bool(r.get('phone'))}"
        )

    scorer = LeadScorer()
    updated = []
    for r in rows:
        cid = int(r["id"])
        # força re-score
        before = r.get("lead_score")
        result = scorer.score_one(cid)
        if not result:
            continue
        after = result.get("lead_score")
        updated.append((cid, before, after, result.get("lead_class"), r.get("name"), r.get("source")))
        print(f"  RESCORE id={cid}: {before} → {after} ({result.get('lead_class')}) {r.get('name')}")

    # só os assigned ao blak
    cur.execute(
        """
        SELECT id, name, source, lead_score, lead_class, city, phone, website_status
        FROM companies WHERE assigned_to = %s
        ORDER BY lead_score DESC NULLS LAST, id DESC
        """,
        (uid,),
    )
    print("\n=== Leads do Blak agora ===")
    for r in cur.fetchall():
        print(
            f"  {r['lead_score']:>3} {r['lead_class'] or '?':6} src={r.get('source') or '?':12} "
            f"{r['name'][:40]} · {r.get('city')}"
        )

    cur.close()
    conn.close()
    print(f"\nTotal re-scored: {len(updated)}")


if __name__ == "__main__":
    main()
