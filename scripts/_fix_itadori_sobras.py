"""Move leads do teste4/itadori para sobras e ajusta plano para barbearia."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import psycopg2
import psycopg2.extras

from src.bot_plan import get_plan, save_plan


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, username, label, active FROM app_users ORDER BY id")
    users = [dict(r) for r in cur.fetchall()]
    print("USERS:")
    for u in users:
        print(" ", u)

    cur.execute(
        """
        SELECT id, username, label FROM app_users
        WHERE lower(username) LIKE %s
           OR lower(label) LIKE %s
           OR lower(username) LIKE %s
           OR lower(label) LIKE %s
           OR lower(username) LIKE %s
        """,
        ("%teste%", "%itadori%", "%itadori%", "%teste%", "%teste4%"),
    )
    match = [dict(r) for r in cur.fetchall()]
    print("MATCH:", match)

    # prefer exact teste4
    uid = None
    for u in users:
        if (u.get("username") or "").lower() == "teste4":
            uid = int(u["id"])
            break
    if uid is None and match:
        uid = int(match[0]["id"])

    if uid is None:
        print("ERRO: usuario teste4/itadori nao encontrado")
        cur.close()
        conn.close()
        return

    cur.execute(
        """
        SELECT id, name, niche, city, state, phone, lead_score, assigned_to
        FROM companies
        WHERE assigned_to = %s
        ORDER BY id DESC
        """,
        (uid,),
    )
    leads = [dict(r) for r in cur.fetchall()]
    print(f"Leads assigned to user_id={uid}: {len(leads)}")
    for L in leads:
        print(" ", L["id"], L.get("name"), L.get("niche"), L.get("city"))

    # unassign all of them to sobras (or last 6 if more?)
    # user said 6 — if more, unassign all for this user that look imobiliaria, else all
    to_free = leads
    if len(leads) > 6:
        # prefer imobiliaria niche first
        imob = [L for L in leads if "imob" in (L.get("niche") or "").lower()]
        if len(imob) >= 6:
            to_free = imob[:6]
        else:
            to_free = leads[:6]

    ids = [int(L["id"]) for L in to_free]
    if ids:
        cur.execute(
            """
            UPDATE companies
            SET assigned_to = NULL, assigned_at = NULL
            WHERE id = ANY(%s)
            RETURNING id, name, niche
            """,
            (ids,),
        )
        freed = [dict(r) for r in cur.fetchall()]
        conn.commit()
        print(f"FREED {len(freed)} -> sobras:")
        for L in freed:
            print(" ", L)
    else:
        print("Nada para liberar")

    # fix bot plan to barbearia only (keep cities or default aracaju)
    plan = get_plan()
    cities = plan.get("city_ids") or []
    # if plan has only imobiliaria cities keep them; else keep as-is
    new_plan = save_plan(
        target_leads=int(plan.get("target_leads") or 20),
        city_ids=cities,
        niche_ids=["salao_barbearia"],
        notes="cockpit/manual: so barbearia (corrigido imobiliaria)",
        updated_by="patrao",
    )
    print("PLAN fixed niches:", new_plan.get("niche_ids"))
    print("PLAN cities:", new_plan.get("city_ids"))
    print("PLAN target:", new_plan.get("target_leads"))

    cur.close()
    conn.close()
    print("OK")


if __name__ == "__main__":
    main()
