"""Checa meta compartilhada e leads recentes ao vivo."""
from __future__ import annotations

import json
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
        "SELECT status, session_leads_count, mission_target, last_job, updated_at "
        "FROM bot_runtime WHERE id = 1"
    )
    rt = dict(cur.fetchone() or {})
    print("=== RUNTIME (meta compartilhada) ===")
    print(rt)

    cur.execute(
        """
        SELECT id, name, source, lead_score, city, niche, assigned_to, scraped_at
        FROM companies
        ORDER BY id DESC
        LIMIT 20
        """
    )
    print("\n=== Últimos 20 leads ===")
    for r in cur.fetchall():
        sa = (r.get("scraped_at") or "")[:19]
        print(
            f"id={r['id']} src={r.get('source') or '?':12} score={r.get('lead_score')} "
            f"assign={r.get('assigned_to')} { (r.get('name') or '')[:36]} · {r.get('city')} · {sa}"
        )

    cur.execute(
        """
        SELECT
          COALESCE(source, '?') AS src,
          COUNT(*) AS n
        FROM companies
        WHERE scraped_at IS NOT NULL
          AND scraped_at::text >= (NOW() - INTERVAL '3 hours')::text
        GROUP BY 1
        ORDER BY n DESC
        """
    )
    print("\n=== Por source (últimas ~3h, se timestamp parseável) ===")
    try:
        for r in cur.fetchall():
            print(f"  {r['src']}: {r['n']}")
    except Exception as e:
        print("  (query falhou)", e)
        conn.rollback()

    cur.close()
    conn.close()

    st_path = ROOT / "data" / "cockpit_state.json"
    if st_path.exists():
        st = json.loads(st_path.read_text(encoding="utf-8"))
        print("\n=== COCKPIT STATE ===")
        for k in (
            "status",
            "session_leads",
            "mission_target",
            "mission_label",
            "current_mission_id",
            "message",
        ):
            print(f"  {k}: {st.get(k)}")
        print("\n=== Últimos 30 logs cockpit ===")
        for L in (st.get("log") or [])[-30:]:
            print(
                f"  {(L.get('t') or '')[11:19]} [{L.get('src') or '?':6}] {(L.get('msg') or '')[:130]}"
            )


if __name__ == "__main__":
    main()
