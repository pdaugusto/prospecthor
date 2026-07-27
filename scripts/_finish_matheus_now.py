"""
Para lógica de missão Matheus (se possível), remove lixo (Hapvida/hospitais),
completa meta 20 com leads bons do banco gerados / sobras.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv(ROOT / ".env")

MATHEUS_ID = 17
META = 20
MISSION_ID = "8b6129d3"
NICHES = [
    "advocacia",
    "clinica_medica",
    "fisioterapia",
    "hotel",
    "joalheria",
    "seguradora",
]

# empresas gigantes / redes — não são lead tocável
_GIANT_RE = re.compile(
    r"\b("
    r"hapvida|notre\s*dame|interm[eé]dica|amil|sulam[eé]rica|bradesco\s*sa[uú]de|"
    r"unimed|prevent\s*senior|porto\s*seguro\s*sa[uú]de|"
    r"hospital\s+(albert\s+einstein|s[ií]rio|s[ií]rio[\-\s]?liban[eê]s|"
    r"o\s*swaldo\s*cruz|s[aã]o\s*lu[ií]z|samaritano|moinhos\s*de\s*vento|"
    r"m[aã]e\s*de\s*deus|ernesto\s*dornelles|divina\s*provid[eê]ncia|"
    r"das\s*cl[ií]nicas|universit[aá]rio|regional|municipal|estadual|"
    r"geral|federal|militar|santa\s*casa)|"
    r"santa\s*casa|"
    r"rede\s*d['\u2019]?or|dasa|fleury|lavoisier|hermes\s*pardini|"
    r"grupo\s*fleury|diagn[oó]sticos\s*da\s*am[eé]rica|"
    r"einstein|s[ií]rio[\-\s]?liban[eê]s|"
    r"sus\b|upa\b|pronto[\-\s]?socorro\s*(municipal|estadual|geral)|"
    r"prefeitura|secretaria\s+de\s+sa[uú]de|"
    r"universidade\s|faculdade\s+de\s+medicina"
    r")\b",
    re.I,
)

_GIANT_CATEGORY = re.compile(
    r"hospital|pronto[\-\s]?socorro|upa\b|emergency|medical\s*center\s*group",
    re.I,
)


def is_giant_enterprise(row: dict) -> bool:
    name = (row.get("name") or "")
    cat = (row.get("category") or "")
    if _GIANT_RE.search(name):
        return True
    # "Hospital X" genérico + redes
    if re.search(r"\bhospital\b", name, re.I) and not re.search(
        r"cl[ií]nica|consult[oó]rio|fisio|odont", name, re.I
    ):
        # hospital sozinho costuma ser grande; clínicas de fisio ok
        if re.search(r"hospital", name, re.I):
            return True
    if _GIANT_CATEGORY.search(cat) and re.search(r"hospital|upa|pronto", name, re.I):
        return True
    return False


def has_whatsapp_or_instagram(row: dict) -> bool:
    """Só telefone fixo sem WA/IG = ruim (sem contato real)."""
    ig_u = (row.get("instagram_url") or "").strip()
    ig_n = (row.get("instagram_username") or "").strip()
    if ig_u or ig_n:
        return True
    phone = re.sub(r"\D", "", str(row.get("phone") or ""))
    if not phone:
        return False
    # celular BR: 10/11 dígitos com 9 após DDD, ou 12/13 com 55
    if phone.startswith("55") and len(phone) >= 12:
        # 55 + DDD(2) + 9xxxxxxxx
        local = phone[2:]
        if len(local) >= 10 and local[2] == "9":
            return True
        if len(local) == 11 and local[2] == "9":
            return True
    if len(phone) == 11 and phone[2] == "9":
        return True
    # 10 dígitos antigos celulares raros — não conta como WA confiável
    return False


def stop_cockpit_files() -> None:
    """Marca missão parada + estado cockpit parado (kill de PID se possível)."""
    miss_p = ROOT / "data" / "missions.json"
    state_p = ROOT / "data" / "cockpit_state.json"
    now = datetime.now().isoformat(timespec="seconds")

    if miss_p.exists():
        data = json.loads(miss_p.read_text(encoding="utf-8"))
        for m in data.get("missions") or []:
            if m.get("id") == MISSION_ID or (
                str(m.get("username") or "").lower().find("matheus") >= 0
                and m.get("status") == "rodando"
            ):
                m["status"] = "parada"
                m["finished_at"] = now
                m["notes"] = (m.get("notes") or "") + " | parado manual + fill banco"
                print(f"missão {m.get('id')} → parada")
        data["updated_at"] = now
        miss_p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    pids = []
    if state_p.exists():
        st = json.loads(state_p.read_text(encoding="utf-8"))
        for k in ("pid", "pid_fonte_b"):
            if st.get(k):
                pids.append(int(st[k]))
        st["status"] = "parado"
        st["message"] = "Parado manual — Matheus completado do banco"
        st["pid"] = None
        st["pid_fonte_b"] = None
        st["current_mission_id"] = None
        log = st.get("log") or []
        log.append(
            {
                "t": now,
                "level": "WARN",
                "msg": "⏹ Missão Matheus parada manualmente + fill do banco",
                "bot": False,
                "src": "sys",
            }
        )
        st["log"] = log[-200:]
        state_p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        print("cockpit_state → parado")

    # tenta matar PIDs no Windows
    for pid in pids:
        try:
            import subprocess

            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            print(f"taskkill pid={pid}")
        except Exception as exc:
            print(f"taskkill fail {pid}: {exc}")

    # bot_runtime
    try:
        from src.bot_status import set_status, set_mission_meta, add_log

        set_status("parado", last_job="matheus parada manual + fill")
        set_mission_meta(0, reset_leads=False)
        add_log("Missão Matheus parada manual + fill banco", level="WARN")
    except Exception as exc:
        print(f"bot_status: {exc}")


def main() -> None:
    stop_cockpit_files()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # desassign gigantes / só-fixo ruins do Matheus
    cur.execute(
        """
        SELECT id, name, category, phone, instagram_url, instagram_username,
               niche, city, lead_score
        FROM companies WHERE assigned_to = %s
        ORDER BY id
        """,
        (MATHEUS_ID,),
    )
    owned = [dict(r) for r in cur.fetchall()]
    removed = 0
    kept_good = []
    for r in owned:
        bad = is_giant_enterprise(r) or not has_whatsapp_or_instagram(r)
        if bad:
            cur.execute(
                """
                UPDATE companies SET assigned_to = NULL, assigned_at = NULL
                WHERE id = %s
                """,
                (r["id"],),
            )
            removed += 1
            reason = "gigante" if is_giant_enterprise(r) else "sem WA/IG"
            print(f"  ✗ remove {r['id']} {r.get('name')!r} ({reason})")
        else:
            kept_good.append(r)
    conn.commit()
    print(f"Matheus tinha {len(owned)}; removeu {removed}; bons {len(kept_good)}")

    need = max(0, META - len(kept_good))
    print(f"precisa +{need} para meta {META}")

    if need > 0:
        # candidatos: livres nos nichos da missão OU qualquer livre bom recente
        cur.execute(
            """
            SELECT id, name, category, phone, instagram_url, instagram_username,
                   niche, city, lead_score, source, created_at
            FROM companies
            WHERE assigned_to IS NULL
              AND (business_status IS NULL OR business_status != 'CLOSED_PERMANENTLY')
              AND (
                    lead_class = 'raio'
                    OR website_status IN ('sem_site', 'so_social')
                    OR website IS NULL
                    OR TRIM(COALESCE(website, '')) = ''
              )
            ORDER BY
              CASE WHEN niche = ANY(%s) THEN 0 ELSE 1 END,
              lead_score DESC NULLS LAST,
              id DESC
            LIMIT 400
            """,
            (NICHES,),
        )
        cands = [dict(r) for r in cur.fetchall()]
        print(f"candidatos livres: {len(cands)}")

        assigned = 0
        now = datetime.now().isoformat()
        for r in cands:
            if assigned >= need:
                break
            if is_giant_enterprise(r):
                continue
            if not has_whatsapp_or_instagram(r):
                continue
            cur.execute(
                """
                UPDATE companies
                SET assigned_to = %s, assigned_at = %s
                WHERE id = %s AND assigned_to IS NULL
                """,
                (MATHEUS_ID, now, r["id"]),
            )
            if cur.rowcount:
                assigned += 1
                print(
                    f"  ✓ +{assigned} id={r['id']} score={r.get('lead_score')} "
                    f"{r.get('name')!r} · {r.get('niche')}/{r.get('city')}"
                )
        conn.commit()
        print(f"atribuídos agora: {assigned}")

    cur.execute("SELECT COUNT(*) AS n FROM companies WHERE assigned_to = %s", (MATHEUS_ID,))
    total = int(cur.fetchone()["n"])
    print(f"\n=== Matheus final: {total}/{META} ===")

    cur.execute(
        """
        SELECT niche, COUNT(*) n FROM companies WHERE assigned_to = %s
        GROUP BY niche ORDER BY n DESC
        """,
        (MATHEUS_ID,),
    )
    for r in cur.fetchall():
        print(f"  {r['niche']}: {r['n']}")

    # marca missão ok se cheio
    miss_p = ROOT / "data" / "missions.json"
    if miss_p.exists() and total >= META:
        data = json.loads(miss_p.read_text(encoding="utf-8"))
        for m in data.get("missions") or []:
            if m.get("id") == MISSION_ID:
                m["status"] = "ok"
                m["leads_found"] = total
                m["finished_at"] = datetime.now().isoformat(timespec="seconds")
                m["notes"] = (m.get("notes") or "") + f" | fill manual {total}"
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        miss_p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("missão marcada ok")

    # bot meta done
    try:
        from src.bot_status import set_mission_meta, set_status

        set_mission_meta(META, reset_leads=False)
        # força contador visual
        conn2 = psycopg2.connect(os.environ["DATABASE_URL"])
        c2 = conn2.cursor()
        c2.execute(
            """
            UPDATE bot_runtime SET
                session_leads_count = %s,
                mission_target = %s,
                last_job = %s,
                status = 'parado',
                updated_at = %s
            WHERE id = 1
            """,
            (
                META,
                META,
                f"matheus fill manual {total}/{META}",
                datetime.now().isoformat(),
            ),
        )
        conn2.commit()
        c2.close()
        conn2.close()
    except Exception as exc:
        print(f"runtime: {exc}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
