"""
Varre o banco e prepara TODAS as sobras (assigned_to NULL):
  - score sem assign
  - tira gigante / só fixo (opcional: marca notes, não apaga)
  - resumo do pool

Uso: python scripts/_dump_all_to_sobras.py
"""
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
    from src.contact import (
        has_usable_contact,
        is_giant_enterprise,
        is_mobile_phone,
        has_instagram,
        enrich_contact_fields,
    )
    from src.scorer import LeadScorer
    from src.users import lead_has_client_contact, has_contact_phone

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # total picture
    cur.execute("SELECT COUNT(*) n FROM companies")
    total_all = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) n FROM companies WHERE assigned_to IS NULL")
    free_n = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) n FROM companies WHERE assigned_to IS NOT NULL")
    assigned_n = int(cur.fetchone()["n"])
    print(f"banco: total={total_all} assigned={assigned_n} livres={free_n}")

    cur.execute(
        """
        SELECT *
        FROM companies
        WHERE assigned_to IS NULL
        ORDER BY id DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    print(f"processando {len(rows)} livres...")

    scorer = LeadScorer()
    scored = 0
    good = 0
    weak_contact = 0
    giant = 0
    closed = 0
    errors = 0

    for r in rows:
        rid = int(r["id"])
        name = r.get("name") or "?"
        if (r.get("business_status") or "").upper() == "CLOSED_PERMANENTLY":
            closed += 1
            continue

        # enriquece contato em memória
        try:
            enrich_contact_fields(r)
        except Exception:
            pass

        if is_giant_enterprise(r):
            giant += 1
            # deixa no banco mas não scoreia como raio “bom” — só conta
            print(f"  gigante id={rid} {name!r}")
            continue

        usable = has_usable_contact(r) or lead_has_client_contact(r)
        if not usable:
            # tem algo? fixo só?
            if has_contact_phone(r.get("phone")) and not is_mobile_phone(r.get("phone")) and not has_instagram(r):
                weak_contact += 1
            else:
                weak_contact += 1
            # ainda scoreia se sem site (fica no pool do patrão ver)
            pass

        try:
            # force score se sem score ou sem lead_class
            need = (not r.get("scored_at")) or (r.get("lead_score") is None) or (
                not (r.get("lead_class") or "").strip()
            )
            # re-score raios sem site para escala atual
            is_sem_site = (
                (r.get("website_status") or "") in ("sem_site", "so_social", "")
                or not (r.get("website") or "").strip()
            )
            if need or is_sem_site:
                scorer.score_one(rid, assign=False)
                scored += 1
            good += 1 if usable else 0
        except Exception as exc:
            errors += 1
            print(f"  err id={rid}: {exc}")

    conn.commit()

    # resumo final por nicho (livres + úteis)
    cur.execute(
        """
        SELECT niche, COUNT(*) n
        FROM companies
        WHERE assigned_to IS NULL
          AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
        GROUP BY niche
        ORDER BY n DESC
        """
    )
    print("\n=== SOBRAS por nicho (assigned_to NULL) ===")
    by_niche = cur.fetchall()
    for r in by_niche:
        print(f"  {r['niche'] or '(sem nicho)'}: {r['n']}")

    cur.execute(
        """
        SELECT COUNT(*) n FROM companies
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
    raios = int(cur.fetchone()["n"])

    cur.execute(
        """
        SELECT id, name, city, niche, lead_score, phone, instagram_username, source
        FROM companies
        WHERE assigned_to IS NULL
          AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
        ORDER BY lead_score DESC NULLS LAST, id DESC
        LIMIT 30
        """
    )
    print("\n=== top 30 sobras (score) ===")
    for r in cur.fetchall():
        print(
            f"  id={r['id']} sc={r.get('lead_score')} {r.get('name')!r} · "
            f"{r.get('niche')}/{r.get('city')} · tel={r.get('phone') or '-'} "
            f"ig={r.get('instagram_username') or '-'}"
        )

    print(
        f"\nDone. livres={free_n} scoreados_agora={scored} "
        f"com_contato_util≈{good} fracos={weak_contact} gigantes={giant} "
        f"fechados={closed} erros={errors} raios/sem_site={raios}"
    )
    print("Tudo que está com assigned_to NULL já é SOBRA sua (pool do Patrão).")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
