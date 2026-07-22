"""
Cockpit local do Patrão — opera o bot sem CMD.

  .\venv\Scripts\python.exe cockpit\app.py
  ou: Abrir-Cockpit.bat

http://127.0.0.1:5055
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
MISSIONS_PATH = DATA / "missions.json"
STATE_PATH = DATA / "cockpit_state.json"
PORT = int(os.getenv("COCKPIT_PORT") or "5055")

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

_lock = threading.Lock()
_worker: threading.Thread | None = None
_stop_flag = threading.Event()
_proc: subprocess.Popen | None = None
_proc_fonte_b: subprocess.Popen | None = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_missions() -> list[dict]:
    data = _load_json(MISSIONS_PATH, {"missions": []})
    return list(data.get("missions") or [])


def save_missions(missions: list[dict]) -> None:
    _save_json(MISSIONS_PATH, {"missions": missions, "updated_at": _now()})


def load_state() -> dict:
    st = _load_json(
        STATE_PATH,
        {
            "status": "parado",
            "message": "Cockpit pronto",
            "pid": None,
            "current_mission_id": None,
            "session_leads": 0,
            "mission_target": 0,
            "mission_label": "",
            "log": [],
        },
    )
    st.setdefault("session_leads", 0)
    st.setdefault("mission_target", 0)
    st.setdefault("mission_label", "")
    return st


def save_state(state: dict) -> None:
    _save_json(STATE_PATH, state)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# linhas "úteis" do bot (não poluir com debug demais)
_KEEP_HINTS = (
    "▶",
    "⏭",
    "meta",
    "lead",
    "Lead",
    "Fila",
    "Plano",
    "Sessão",
    "job",
    "bairro",
    "OK ",
    "ok:",
    "ERRO",
    "Error",
    "Falha",
    "encontr",
    "sem site",
    "Raio",
    "score",
    "Score",
    "Inici",
    "finaliz",
    "parada",
    "Bot ",
    "pipeline",
    "Pipeline",
    "cota",
    "Cota",
    "revez",
    "Missão",
    "missao",
    "Fonte B",
    "fonte b",
    "fonte-b",
    "OSM",
    "OpenStreet",
    "Maps",
    "mapa",
    "SALVO",
    "contatável",
    "contatavel",
    "sem tel",
    "sem site",
    "painel",
    "worker",
    "INÍCIO",
    "FIM",
    "+",
)


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s or "").strip()


def _line_level(line: str) -> str:
    u = line.upper()
    if "ERROR" in u or "ERRO" in u or "TRACEBACK" in u or "FALHA" in u:
        return "ERROR"
    if "WARN" in u or "⚠" in u or "NÃO CONSEGUI" in u or "NENHUM LEAD" in u:
        return "WARN"
    return "INFO"


def _extract_bot_message(line: str) -> str:
    """Tira lixo do Loguru e deixa só a mensagem legível."""
    clean = _strip_ansi(line)
    if not clean:
        return ""
    # [B] prefix do worker Fonte B
    is_b = clean.startswith("[B]") or clean.startswith("[b]")
    if is_b:
        clean = clean[3:].strip()
    # Loguru colorido: 2026-... | INFO | msg  OR  [INFO] msg
    if " | " in clean:
        parts = [p.strip() for p in clean.split(" | ")]
        # pega a última parte que parece mensagem (não é só nível/tempo)
        for p in reversed(parts):
            if p.upper() in ("INFO", "DEBUG", "WARNING", "ERROR", "SUCCESS"):
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}", p):
                continue
            if re.match(r"^\[\w+\]$", p):
                continue
            clean = p
            break
    # [Playwright] / [GoogleMapsSearcher] no começo — deixa
    clean = re.sub(r"^\s*\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}[^\]]*\]\s*", "", clean)
    clean = re.sub(r"^\s*\[(INFO|DEBUG|WARNING|ERROR|WARN)\]\s*", "", clean, flags=re.I)
    # prefixo módulo loguru: name:function:line —
    clean = re.sub(r"^\s*[\w\.]+:\w+:\d+\s*[-–—]\s*", "", clean)
    if is_b and not clean.upper().startswith("[FONTE"):
        if "Fonte B" not in clean and "fonte b" not in clean.lower():
            clean = "[Fonte B] " + clean
    return clean.strip()


def _humanize_bot_line(line: str) -> str | None:
    """
    Converte linha técnica em texto curto pro cockpit.
    None = descartar (ruído).
    """
    msg = _extract_bot_message(line)
    if not msg or len(msg) < 3:
        return None
    low = msg.lower()

    # descarta ruído puro
    if any(x in low for x in ("debug", "traceback", "site-packages", "urllib3", "awaiting")):
        if "error" not in low and "erro" not in low:
            return None

    # reescritas amigáveis (padrão → texto)
    rules: list[tuple[str, str]] = [
        (r"fonte b.*in[ií]cio", "📦 Fonte B (mapa/CNPJ) começou"),
        (r"fonte b.*fim", msg),  # já é bom
        (r"worker fonte b", "📦 Fonte B ligada em paralelo ao Google Maps"),
        (r"buscando no mapa", "📦 Fonte B: consultando OpenStreetMap…"),
        (r"cidade localizada", "📦 Fonte B: cidade encontrada no mapa"),
        (r"mapa devolveu\s+(\d+)", r"📦 Fonte B: \1 empresas no mapa"),
        (r"zero no mapa", "📦 Fonte B: nada no mapa para este nicho/cidade"),
        (r"nenhum com telefone ou instagram", "📦 Fonte B: achou no mapa, mas sem tel/IG — não grava"),
        (r"✓ salvo:", "✅ Fonte B salvou lead:"),
        (r"salvo:", "✅ Fonte B salvou lead:"),
        (r"nenhum lead novo", "📦 Fonte B: 0 leads novos nesta rodada (normal se o mapa não tem tel)"),
        (r"plano vazio", "⚠ Fonte B: missão sem cidade/nicho no plano"),
        (r"playwright.*abrir", "🗺 Maps: abrindo busca no Google…"),
        (r"tem site.*pula", "🗺 Maps: empresa com site — pulou"),
        (r"sem tel/ig.*não conta", "🗺 Maps: sem telefone/Instagram — não conta na meta"),
        (r"lead contatável", "✅ Maps: lead contatável"),
        (r"✓ lead contatável", "✅ Maps: lead contatável"),
        (r"parar cedo", "🗺 Maps: área fraca — parou cedo e vai pro próximo bairro"),
        (r"meta de.*leads", msg),
        (r"miss[aã]o.*terminou", msg),
        (r"fila\s+\d+/\d+", msg),
    ]
    for pat, repl in rules:
        if re.search(pat, low, re.I):
            if "\\" in repl:
                msg = re.sub(pat, repl, msg, flags=re.I)
            elif repl == msg:
                pass
            else:
                # se o repl for prefixo curto, anexa o resto útil
                if repl.startswith(("📦", "✅", "🗺", "⚠")) and ":" not in repl[-3:]:
                    # keep original detail after first colon if any
                    if ":" in msg:
                        detail = msg.split(":", 1)[-1].strip()
                        msg = f"{repl} {detail}" if detail else repl
                    else:
                        msg = repl
                else:
                    msg = repl
            break

    # prefixo Maps vs Fonte B se ainda não tiver
    if msg.startswith("[Fonte B]") or msg.startswith("📦") or msg.startswith("✅ Fonte"):
        pass
    elif "fonte b" in low or msg.startswith("[B]"):
        if not msg.startswith("📦"):
            msg = "📦 " + msg.lstrip("📦 ")
    elif any(k in low for k in ("playwright", "googlemaps", "maps searcher", "pipeline")):
        if not msg.startswith(("🗺", "✅", "⚠")):
            msg = "🗺 " + msg

    # encurta
    if len(msg) > 220:
        msg = msg[:217] + "…"
    return msg


def _should_keep_bot_line(line: str) -> bool:
    if not line or len(line) < 3:
        return False
    if _line_level(line) == "ERROR":
        return True
    low = line.lower()
    # Fonte B sempre (quando passa no extract)
    if "fonte b" in low or "[b]" in low or "openstreet" in low:
        return True
    for h in _KEEP_HINTS:
        if h.lower() in low:
            return True
    if re.search(r"\b\d+\s*leads?\b", low):
        return True
    if re.search(r"[✓✅⏹📦🗺]", line):
        return True
    return False


def _parse_session_leads(line: str, current: int) -> int:
    """Tenta extrair contagem de leads da sessão a partir do log do bot."""
    # ex: leads_sessão=12/20  |  sessão 12/20  |  +3 leads ... (sessão 12
    m = re.search(r"leads[_ ]sess[aã]o[=:\s]+(\d+)", line, re.I)
    if m:
        return max(current, int(m.group(1)))
    m = re.search(r"sess[aã]o\s+(\d+)\s*/\s*\d+", line, re.I)
    if m:
        return max(current, int(m.group(1)))
    m = re.search(r"\+(\d+)\s*leads?", line, re.I)
    if m:
        return current + int(m.group(1))
    m = re.search(r"(\d+)\s*leads?\s*NOVOS", line, re.I)
    if m:
        return max(current, int(m.group(1)))
    return current


def _log_source(msg: str, *, from_bot: bool = False, force_src: str | None = None) -> str:
    """Classifica a linha: maps | fonteb | sys (para cor/painel no cockpit)."""
    if force_src in ("maps", "fonteb", "sys"):
        return force_src
    low = (msg or "").lower()
    # Maps tem prioridade se a linha é claramente Google/Playwright
    if any(
        k in low
        for k in (
            "playwright",
            "googlemapssearcher",
            "google maps",
            "[playwright]",
            "lead contat",
            "maps.google",
            "maps/place",
        )
    ):
        return "maps"
    if (
        "fonte b" in low
        or low.startswith("📦")
        or low.startswith("[b]")
        or "openstreet" in low
        or ("cnpj" in low and "fonte" in low)
        or "worker fonte" in low
        or "overpass" in low
    ):
        return "fonteb"
    if from_bot or low.startswith("🗺") or "pipeline" in low:
        if "fonte b" in low:
            return "fonteb"
        if from_bot or any(
            k in low
            for k in ("playwright", "maps", "🗺", "sem site", "bairro", "job")
        ):
            return "maps"
    return "sys"


def append_log(
    msg: str,
    level: str = "INFO",
    *,
    from_bot: bool = False,
    src: str | None = None,
) -> None:
    raw = _strip_ansi(str(msg))
    if not raw:
        return
    if from_bot:
        if not _should_keep_bot_line(raw):
            return
        clean = _humanize_bot_line(raw)
        if not clean:
            return
    else:
        clean = raw
    source = _log_source(clean if from_bot else raw, from_bot=from_bot, force_src=src)
    # se a linha humanizada tem 📦, força fonteb
    if clean.startswith("📦") or "✅ Fonte B" in clean:
        source = "fonteb"
    elif clean.startswith("🗺") or (clean.startswith("✅") and "Fonte B" not in clean and from_bot):
        if "fonte b" not in clean.lower():
            source = "maps"

    st = load_state()
    lines = list(st.get("log") or [])
    # evita spam da mesma linha repetida no mesmo src
    if lines and lines[-1].get("msg") == clean[:500] and lines[-1].get("src") == source:
        return
    lines.append(
        {
            "t": _now(),
            "level": level,
            "msg": clean[:500],
            "bot": bool(from_bot),
            "src": source,
        }
    )
    st["log"] = lines[-300:]
    if not from_bot:
        st["message"] = clean[:200]
    else:
        leads = int(st.get("session_leads") or 0)
        leads = _parse_session_leads(clean, leads)
        st["session_leads"] = leads
        target = int(st.get("mission_target") or 0)
        meta_txt = f"{leads}/{target}" if target else str(leads)
        if source == "fonteb":
            st["message"] = f"Meta {meta_txt} · {clean[:160]}"
        elif leads or target:
            st["message"] = f"Meta {meta_txt}" + (
                f" · {clean[:140]}" if source == "maps" and clean else ""
            )
        elif source == "maps":
            st["message"] = clean[:200]
    save_state(st)
    if not from_bot:
        try:
            from src.bot_status import add_log

            add_log(f"[cockpit] {clean}", level=level)
        except Exception:
            pass


def _pump_bot_output(proc: subprocess.Popen) -> None:
    """Lê stdout/stderr do bot e joga no log do cockpit ao vivo."""
    def _reader(stream, label: str) -> None:
        try:
            for raw in iter(stream.readline, ""):
                if raw is None:
                    break
                line = _strip_ansi(raw)
                if not line:
                    continue
                append_log(line, _line_level(line), from_bot=True, src="maps")
        except Exception as exc:
            append_log(f"(log do Maps interrompido: {exc})", "WARN", src="maps")
        finally:
            try:
                stream.close()
            except Exception:
                pass

    if proc.stdout:
        threading.Thread(target=_reader, args=(proc.stdout, "out"), daemon=True).start()
    if proc.stderr:
        threading.Thread(target=_reader, args=(proc.stderr, "err"), daemon=True).start()


def python_exe() -> str:
    venv_py = ROOT / "venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def is_bot_process_alive() -> bool:
    global _proc
    if _proc is not None:
        if _proc.poll() is None:
            return True
        _proc = None
    st = load_state()
    pid = st.get("pid")
    if not pid:
        return False
    alive = False
    try:
        if sys.platform == "win32":
            # tasklist filtra pelo PID
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = (r.stdout or "") + (r.stderr or "")
            alive = str(pid) in out and "INFO:" not in out.upper()
        else:
            os.kill(int(pid), 0)
            alive = True
    except Exception:
        alive = False
    if not alive:
        st["pid"] = None
        if st.get("status") == "rodando" and not (_worker and _worker.is_alive()):
            st["status"] = "parado"
        save_state(st)
    return alive


@app.get("/")
def home():
    return render_template("cockpit.html", port=PORT)


@app.get("/api/catalog")
def api_catalog():
    from src.bot_plan import load_catalog
    from src.users import list_users

    cat = load_catalog()
    users = []
    try:
        for u in list_users():
            un = (u.get("username") or "").lower()
            if un == "patrao":
                continue
            users.append(
                {
                    "id": u.get("id"),
                    "username": u.get("username"),
                    "label": u.get("label") or u.get("username"),
                    "active": u.get("active"),
                    "quota": u.get("monthly_quota"),
                    "used": u.get("assigned_this_month"),
                }
            )
    except Exception as exc:
        return jsonify({"error": str(exc), "users": [], "catalog": cat}), 500
    return jsonify({"users": users, "catalog": cat})


@app.get("/api/missions")
def api_missions():
    return jsonify({"missions": load_missions(), "state": load_state()})


@app.post("/api/missions")
def api_add_mission():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    niche_ids = body.get("niche_ids") or []
    city_ids = body.get("city_ids") or []
    target = int(body.get("target_leads") or 20)
    label = (body.get("label") or "").strip()

    if not niche_ids or not city_ids:
        return jsonify({"error": "Escolha pelo menos 1 nicho e 1 cidade."}), 400
    if target < 1:
        return jsonify({"error": "Meta de leads deve ser >= 1."}), 400

    username = body.get("username") or ""
    if user_id not in (None, "", 0, "0"):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return jsonify({"error": "user_id inválido"}), 400
    else:
        user_id = None

    m = {
        "id": str(uuid.uuid4())[:8],
        "label": label or f"Missão {username or 'livre'}",
        "user_id": user_id,
        "username": username or ("livre" if not user_id else username),
        "niche_ids": [str(x) for x in niche_ids],
        "city_ids": [str(x) for x in city_ids],
        "target_leads": target,
        "status": "pendente",
        "created_at": _now(),
        "finished_at": None,
        "notes": (body.get("notes") or "")[:300],
    }
    missions = load_missions()
    missions.append(m)
    save_missions(missions)
    append_log(f"Missão adicionada: {m['label']} ({m['id']})")
    return jsonify({"ok": True, "mission": m, "missions": missions})


@app.delete("/api/missions/<mid>")
def api_del_mission(mid: str):
    missions = load_missions()
    target = next((m for m in missions if m.get("id") == mid), None)
    if not target:
        return jsonify({"error": "Missão não encontrada."}), 404
    if target.get("status") == "rodando":
        return jsonify({"error": "Não apague missão em execução. Clique em Parar primeiro."}), 400
    missions = [m for m in missions if m.get("id") != mid]
    save_missions(missions)
    append_log(f"Missão removida: {mid} ({target.get('label') or ''})")
    return jsonify({"ok": True, "missions": missions})


@app.put("/api/missions/<mid>")
@app.post("/api/missions/<mid>")
def api_update_mission(mid: str):
    """Edita missão pendente/parada/erro antes de rodar de novo."""
    body = request.get_json(silent=True) or {}
    # POST com action delete (compat)
    if (body.get("action") or "").lower() == "delete":
        return api_del_mission(mid)

    missions = load_missions()
    idx = next((i for i, m in enumerate(missions) if m.get("id") == mid), None)
    if idx is None:
        return jsonify({"error": "Missão não encontrada."}), 404
    m = missions[idx]
    st = (m.get("status") or "").lower()
    if st == "rodando":
        return jsonify({"error": "Missão em execução — não edita. Pare o bot antes."}), 400
    if st not in ("pendente", "parada", "erro", "ok"):
        return jsonify({"error": f"Status {st!r} não permite edição."}), 400

    niche_ids = body.get("niche_ids")
    city_ids = body.get("city_ids")
    if niche_ids is not None:
        if not niche_ids:
            return jsonify({"error": "Escolha pelo menos 1 nicho."}), 400
        m["niche_ids"] = [str(x) for x in niche_ids]
    if city_ids is not None:
        if not city_ids:
            return jsonify({"error": "Escolha pelo menos 1 cidade."}), 400
        m["city_ids"] = [str(x) for x in city_ids]

    if "target_leads" in body:
        try:
            target = int(body.get("target_leads") or 20)
        except (TypeError, ValueError):
            return jsonify({"error": "Meta inválida."}), 400
        if target < 1:
            return jsonify({"error": "Meta de leads deve ser >= 1."}), 400
        m["target_leads"] = target

    if "label" in body:
        lab = (body.get("label") or "").strip()
        if lab:
            m["label"] = lab

    if "user_id" in body or "username" in body:
        user_id = body.get("user_id")
        username = (body.get("username") or "").strip()
        if user_id not in (None, "", 0, "0"):
            try:
                user_id = int(user_id)
            except (TypeError, ValueError):
                return jsonify({"error": "user_id inválido"}), 400
            m["user_id"] = user_id
            m["username"] = username or m.get("username") or str(user_id)
        else:
            m["user_id"] = None
            m["username"] = username or "livre"

    if "notes" in body:
        m["notes"] = (body.get("notes") or "")[:300]

    # reabrir para a fila se estava parada/erro/ok
    if st in ("parada", "erro", "ok"):
        m["status"] = "pendente"
        m["finished_at"] = None

    m["updated_at"] = _now()
    missions[idx] = m
    save_missions(missions)
    append_log(f"Missão editada: {m.get('id')} · {m.get('label')}")
    return jsonify({"ok": True, "mission": m, "missions": missions})


@app.post("/api/missions/reorder")
def api_reorder_missions():
    """
    Define a ordem da fila.
    Body: { "ids": ["id3", "id1", "id2"] } — missões omitidas ficam no fim na ordem antiga.
    """
    body = request.get_json(silent=True) or {}
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Envie ids: [..] na ordem desejada."}), 400

    missions = load_missions()
    by_id = {str(m.get("id")): m for m in missions}
    new_list: list[dict] = []
    seen: set[str] = set()
    for raw in ids:
        mid = str(raw)
        if mid in by_id and mid not in seen:
            new_list.append(by_id[mid])
            seen.add(mid)
    for m in missions:
        mid = str(m.get("id"))
        if mid not in seen:
            new_list.append(m)
            seen.add(mid)
    save_missions(new_list)
    append_log(f"Fila reordenada: {len(new_list)} missão(ões)")
    return jsonify({"ok": True, "missions": new_list})


@app.post("/api/missions/clear-done")
def api_clear_done():
    missions = [m for m in load_missions() if m.get("status") in ("pendente", "rodando")]
    save_missions(missions)
    return jsonify({"ok": True, "missions": missions})


def _kill_one(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    pid = proc.pid
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _kill_proc() -> None:
    global _proc, _proc_fonte_b
    _kill_one(_proc)
    _proc = None
    _kill_one(_proc_fonte_b)
    _proc_fonte_b = None


def _start_fonte_b(env: dict, creation: int) -> subprocess.Popen | None:
    """Worker 2: OSM/CNPJ em paralelo (só leads com tel/IG)."""
    global _proc_fonte_b
    try:
        _proc_fonte_b = subprocess.Popen(
            [python_exe(), "-u", str(ROOT / "main.py"), "fonte-b"],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation,
        )
        append_log(
            f"📦 Fonte B ligada em paralelo (mapa/CNPJ) · pid {_proc_fonte_b.pid}",
            src="fonteb",
        )

        def _pump_b() -> None:
            try:
                if not _proc_fonte_b or not _proc_fonte_b.stdout:
                    return
                for line in _proc_fonte_b.stdout:
                    if line:
                        # marca como Fonte B para o humanize
                        append_log(
                            "[B] " + line.rstrip(),
                            _line_level(line),
                            from_bot=True,
                            src="fonteb",
                        )
            except Exception as exc:
                append_log(
                    f"📦 Fonte B: log interrompido ({exc})",
                    "WARN",
                    src="fonteb",
                )

        threading.Thread(target=_pump_b, daemon=True).start()
        return _proc_fonte_b
    except Exception as exc:
        append_log(f"📦 Fonte B NÃO iniciou: {exc}", "WARN", src="fonteb")
        _proc_fonte_b = None
        return None


def _run_one_mission(mission: dict) -> int:
    """Roda Maps (main) + Fonte B em paralelo. Retorna exit code do Maps."""
    global _proc, _proc_fonte_b
    from src.bot_plan import save_plan

    mid = mission.get("id")
    save_plan(
        target_leads=int(mission.get("target_leads") or 20),
        city_ids=list(mission.get("city_ids") or []),
        niche_ids=list(mission.get("niche_ids") or []),
        notes=f"cockpit mission {mid} → {mission.get('username')}",
        updated_by="cockpit",
    )
    append_log(
        f"▶ Missão «{mission.get('label') or mid}» · "
        f"meta {mission.get('target_leads')} leads · "
        f"nichos: {', '.join(mission.get('niche_ids') or []) or '—'} · "
        f"{len(mission.get('city_ids') or [])} cidade(s) · "
        f"dono: {mission.get('username') or 'livre'} · "
        f"2 workers (Google Maps + Fonte B)"
    )

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if mission.get("user_id"):
        env["PROSPECTHOR_FORCE_ASSIGN_TO"] = str(mission["user_id"])
    else:
        env.pop("PROSPECTHOR_FORCE_ASSIGN_TO", None)

    creation = 0
    if sys.platform == "win32":
        creation = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    target = int(mission.get("target_leads") or 20)
    # Meta TOTAL compartilhada (Maps + Fonte B). SEM divisão por nicho.
    try:
        from src.bot_status import set_mission_meta

        set_mission_meta(target, reset_leads=True)
        append_log(
            f"Meta 0/{target} zerada (Maps+Fonte B juntos, sem cota por nicho)",
            src="sys",
        )
    except Exception as exc:
        append_log(f"Aviso meta compartilhada: {exc}", "WARN", src="sys")

    # Maps primeiro (define sessão), Fonte B em seguida (só soma o que falta)
    _proc = subprocess.Popen(
        [python_exe(), "-u", str(ROOT / "main.py"), "run"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation,
    )
    parallel = os.getenv("COCKPIT_PARALLEL_FONTEB", "true").lower() in (
        "1", "true", "yes", "sim",
    )
    if parallel:
        # pequeno atraso para o Maps gravar o plano/status antes da Fonte B
        time.sleep(1.5)
        _start_fonte_b(env, creation)

    st = load_state()
    st["status"] = "rodando"
    st["pid"] = _proc.pid
    st["pid_fonte_b"] = _proc_fonte_b.pid if _proc_fonte_b else None
    st["current_mission_id"] = mid
    st["session_leads"] = 0
    st["mission_target"] = target
    st["mission_label"] = mission.get("label") or mid
    st["message"] = f"Meta 0/{target} · {st['mission_label']}"
    save_state(st)
    append_log(f"Meta da missão: 0/{target} leads (Maps↔Fonte B ligados)", src="sys")
    _pump_bot_output(_proc)

    while _proc.poll() is None:
        if _stop_flag.is_set():
            append_log("Parada pedida — encerrando Maps + Fonte B", "WARN")
            _kill_proc()
            return -1
        try:
            from src.bot_status import get_status, should_stop_for_meta, get_session_leads

            bs = get_status() or {}
            sess = bs.get("session_leads_count")
            st = load_state()
            tgt = int(st.get("mission_target") or target or 0)
            if sess is not None:
                got = int(sess or 0)
                st["session_leads"] = max(int(st.get("session_leads") or 0), got)
                b_alive = _proc_fonte_b is not None and _proc_fonte_b.poll() is None
                if st.get("status") == "rodando":
                    st["message"] = (
                        f"Meta {st['session_leads']}/{tgt or '∞'}"
                        + (" · Maps+Fonte B" if b_alive else " · Maps")
                        + f" · {st.get('mission_label') or mid}"
                    )
                save_state(st)

            # Meta bateu? Só com contagem >= meta (19/20 NÃO para)
            got_now = max(
                int(sess if sess is not None else 0),
                int(st.get("session_leads") or 0),
                get_session_leads(),
            )
            if tgt > 0 and got_now >= tgt:
                # Grace: o claim do último lead sobe a meta ANTES do score/assign.
                # Se matar na hora, o 20º fica sem dono e o cliente fica com 19.
                append_log(
                    f"🛑 META BATEU {got_now}/{tgt} — aguarda 4s p/ score/assign do último, "
                    f"depois encerra os 2",
                    src="sys",
                )
                time.sleep(4.0)
                _kill_proc()
                st = load_state()
                st["session_leads"] = got_now
                st["message"] = f"Meta {got_now}/{tgt} ok — próxima da fila"
                save_state(st)
                try:
                    missions = load_missions()
                    for x in missions:
                        if x.get("id") == mid:
                            x["leads_found"] = got_now
                            x["target_leads"] = tgt
                            break
                    save_missions(missions)
                except Exception:
                    pass
                append_log(
                    f"✓ Missão {mid} ok · meta {got_now}/{tgt} · seguindo fila sem espera",
                    src="sys",
                )
                return 0
        except Exception:
            pass
        time.sleep(0.5)  # reage mais rápido à meta / fila

    code = _proc.returncode if _proc else -1
    try:
        if _proc and _proc.stdout:
            rest = _proc.stdout.read()
            if rest:
                for line in rest.splitlines():
                    append_log(line, _line_level(line), from_bot=True, src="maps")
    except Exception:
        pass
    _proc = None

    # Maps acabou: não fica 2 min esperando Fonte B — fecha e vai pra próxima
    if _proc_fonte_b is not None:
        try:
            from src.bot_status import should_stop_for_meta, get_session_leads as _gsl

            meta_done = False
            try:
                meta_done = should_stop_for_meta() or (
                    target > 0 and _gsl() >= target
                )
            except Exception:
                pass

            if _proc_fonte_b.poll() is None:
                if meta_done:
                    append_log(
                        "📦 Meta ok — encerra Fonte B e vai pra próxima missão",
                        src="fonteb",
                    )
                    _kill_one(_proc_fonte_b)
                else:
                    # espera só uns segundos; se não acabou, mata e segue a fila
                    append_log(
                        "📦 Maps acabou — espera Fonte B no máx. 8s, depois próxima missão",
                        src="fonteb",
                    )
                    for _ in range(8):
                        if _stop_flag.is_set() or _proc_fonte_b.poll() is not None:
                            break
                        try:
                            if should_stop_for_meta():
                                break
                        except Exception:
                            pass
                        time.sleep(1.0)
                    if _proc_fonte_b.poll() is None:
                        append_log(
                            "📦 Fonte B encerrada para não atrasar a fila",
                            "WARN",
                            src="fonteb",
                        )
                        _kill_one(_proc_fonte_b)
                    else:
                        append_log(
                            f"📦 Fonte B encerrou (code={_proc_fonte_b.returncode})",
                            src="fonteb",
                        )
            else:
                append_log(
                    f"📦 Fonte B encerrou (code={_proc_fonte_b.returncode})",
                    src="fonteb",
                )
        except Exception as exc:
            append_log(f"📦 Fonte B: {exc}", "WARN", src="fonteb")
            try:
                _kill_one(_proc_fonte_b)
            except Exception:
                pass
        _proc_fonte_b = None

    st = load_state()
    leads = int(st.get("session_leads") or 0)
    try:
        from src.bot_status import get_session_leads as _gsl2

        leads = max(leads, _gsl2())
    except Exception:
        pass
    tgt = int(st.get("mission_target") or target or 0)
    append_log(
        f"✓ Missão {mid} terminou · meta {leads}/{tgt or '∞'} · próxima da fila já",
        src="sys",
    )
    try:
        missions = load_missions()
        for x in missions:
            if x.get("id") == mid:
                x["leads_found"] = leads
                x["target_leads"] = tgt or x.get("target_leads")
                break
        save_missions(missions)
    except Exception:
        pass
    return int(code if code is not None else -1)


def _pending_in_order(
    missions: list[dict],
    only_ids: list[str] | None = None,
) -> list[dict]:
    """Pendentes na ordem da lista (ou na ordem de only_ids, se informado)."""
    if only_ids:
        by_id = {str(m.get("id")): m for m in missions}
        out: list[dict] = []
        for mid in only_ids:
            m = by_id.get(str(mid))
            if m and (m.get("status") or "") == "pendente":
                out.append(m)
        return out
    return [m for m in missions if (m.get("status") or "") == "pendente"]


def _queue_worker(only_ids: list[str] | None = None) -> None:
    """Roda missões pendentes em sequência. only_ids = só essas (ordem do pedido)."""
    global _worker, _proc
    try:
        # snapshot da seleção (ordem fixa nesta corrida)
        run_ids = [str(x) for x in (only_ids or [])] or None
        total_planned = 0
        if run_ids:
            total_planned = len(
                _pending_in_order(load_missions(), run_ids)
            )
        else:
            total_planned = len(_pending_in_order(load_missions(), None))
        done_n = 0

        while not _stop_flag.is_set():
            missions = load_missions()
            pending = _pending_in_order(missions, run_ids)
            if not pending:
                append_log("Fila vazia — cockpit em espera")
                break
            m = pending[0]
            done_n += 1
            pos = f"{done_n}/{total_planned or len(pending)}"
            append_log(
                f"▶ Fila {pos}: {m.get('id')} · {m.get('label') or ''}"
            )
            # marca rodando
            for x in missions:
                if x.get("id") == m.get("id"):
                    x["status"] = "rodando"
            save_missions(missions)

            st = load_state()
            st["message"] = f"Fila {pos} · {m.get('label') or m.get('id')}"
            st["queue_pos"] = pos
            save_state(st)

            code = _run_one_mission(m)

            missions = load_missions()
            for x in missions:
                if x.get("id") == m.get("id"):
                    if _stop_flag.is_set() or code == -1:
                        x["status"] = "parada"
                    elif code == 0:
                        x["status"] = "ok"
                    else:
                        x["status"] = "erro"
                    x["finished_at"] = _now()
            save_missions(missions)

            if _stop_flag.is_set():
                break
            append_log(f"✓ Missão «{m.get('label') or m.get('id')}» finalizada · {pos}")
            # próxima já — sem pausa
            more = _pending_in_order(load_missions(), run_ids)
            if more:
                append_log(
                    f"→ Próxima agora: «{more[0].get('label') or more[0].get('id')}» "
                    f"({done_n + 1}/{total_planned or (done_n + len(more))})",
                    src="sys",
                )
            else:
                append_log("Fila sem mais pendentes — fim.", src="sys")

        st = load_state()
        st["status"] = "parado"
        st["pid"] = None
        st["current_mission_id"] = None
        st["queue_pos"] = None
        st["message"] = "Parado" if _stop_flag.is_set() else "Fila concluída"
        save_state(st)
        try:
            from src.bot_status import set_status

            set_status("parado", last_job=st["message"])
        except Exception:
            pass
    finally:
        _worker = None
        _proc = None
        _stop_flag.clear()
        st = load_state()
        st["run_mission_ids"] = None
        save_state(st)


@app.post("/api/run")
def api_run():
    """
    Inicia a fila.
    Body opcional: { "mission_ids": ["abc", "def"] }
      — roda só essas (na ordem enviada), se estiverem pendentes.
      — sem mission_ids: todas as pendentes na ordem da fila.
    """
    global _worker
    body = request.get_json(silent=True) or {}
    raw_ids = body.get("mission_ids") or body.get("ids") or []
    if isinstance(raw_ids, (str, int)):
        raw_ids = [raw_ids]
    only_ids = [str(x) for x in raw_ids if str(x).strip()] or None

    with _lock:
        if _worker and _worker.is_alive():
            return jsonify(
                {
                    "error": "Já tem fila rodando no cockpit. Espere terminar ou clique em Parar.",
                    "code": "queue_running",
                }
            ), 409
        if is_bot_process_alive():
            return jsonify(
                {
                    "error": "Já existe bot ativo (talvez no CMD antigo). Clique em ■ Parar e tente de novo. "
                    "Se ainda falhar, feche o CMD do `python main.py run`.",
                    "code": "bot_alive",
                }
            ), 409

        missions = load_missions()
        pending = _pending_in_order(missions, only_ids)
        if not pending:
            all_m = missions
            if only_ids:
                return jsonify(
                    {
                        "error": "Nenhuma das missões selecionadas está PENDENTE. "
                        "Edite (vira pendente) ou marque outras.",
                        "code": "no_pending_selected",
                    }
                ), 400
            if all_m and not any(m.get("status") == "pendente" for m in all_m):
                return jsonify(
                    {
                        "error": "Nenhuma missão PENDENTE na fila. "
                        "Adicione de novo, edite uma parada/erro, ou limpe e recrie.",
                        "code": "no_pending",
                    }
                ), 400
            return jsonify({"error": "Fila vazia. Adicione uma missão.", "code": "empty"}), 400

        _stop_flag.clear()
        st = load_state()
        st["status"] = "rodando"
        st["run_mission_ids"] = only_ids
        if only_ids:
            st["message"] = f"Iniciando {len(pending)} missão(ões) selecionada(s)…"
        else:
            st["message"] = f"Iniciando fila ({len(pending)} pendente(s))…"
        save_state(st)
        _worker = threading.Thread(
            target=_queue_worker,
            kwargs={"only_ids": only_ids},
            daemon=True,
        )
        _worker.start()
        if only_ids:
            append_log(
                f"Fila seletiva: {len(pending)} missão(ões) → "
                + ", ".join(m.get("id") or "?" for m in pending)
            )
        else:
            append_log(f"Fila completa iniciada: {len(pending)} missão(ões)")
    return jsonify({"ok": True, "state": load_state(), "count": len(pending)})


@app.post("/api/stop")
def api_stop():
    _stop_flag.set()
    _kill_proc()
    try:
        from src.bot_status import force_parado

        force_parado(reason="cockpit: parar")
    except Exception:
        try:
            from src.bot_status import set_status

            set_status("parado", last_job="parado pelo cockpit")
        except Exception:
            pass
    st = load_state()
    st["status"] = "parado"
    st["pid"] = None
    st["message"] = "Parado pelo cockpit"
    save_state(st)
    # missões rodando → parada
    missions = load_missions()
    for m in missions:
        if m.get("status") == "rodando":
            m["status"] = "parada"
            m["finished_at"] = _now()
    save_missions(missions)
    append_log("Bot parado pelo cockpit", "WARN")
    return jsonify({"ok": True, "state": st})


@app.post("/api/score")
def api_score():
    """Pontua leads pendentes (útil após Ctrl+C). Não compete com run se bot ativo."""
    if is_bot_process_alive():
        return jsonify(
            {"error": "Bot ainda rodando. Pare antes de rodar só o score (ou espere)."}
        ), 409

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))

    def _job():
        try:
            from src.scorer import LeadScorer

            scorer = LeadScorer()
            if force:
                append_log("Score FORCE: recalculando raios / Maps / OSM / CNPJ…")
                n2 = len(scorer.score_all(force=True) or [])
                msg = (
                    f"Score force: {n2} leads recalculados"
                    if n2
                    else "Score force: 0 leads (nenhum raio/OSM/CNPJ no banco)"
                )
                append_log(msg)
                st = load_state()
                st["message"] = msg
                save_state(st)
            else:
                append_log("Score: só pendentes (sem scored_at)…")
                n1 = len(scorer.score_all(force=False) or [])
                if n1:
                    msg = f"Score: {n1} pendentes pontuados"
                else:
                    msg = (
                        "Nada pendente — todos já têm score. "
                        "Use «Score force» para recalcular (ex.: CNPJ com nota errada)."
                    )
                append_log(msg)
                st = load_state()
                st["message"] = msg
                save_state(st)
            append_log("Score finalizado")
        except Exception as exc:
            append_log(f"Score erro: {exc}", "ERROR")

    threading.Thread(target=_job, daemon=True).start()
    return jsonify(
        {
            "ok": True,
            "message": "Score force em andamento…" if force else "Score pendentes em andamento…",
        }
    )


@app.get("/api/status")
def api_status():
    bot = {}
    try:
        from src.bot_status import get_status

        bot = get_status() or {}
    except Exception:
        try:
            from src.bot_status import ensure_schema
            import psycopg2
            import psycopg2.extras

            ensure_schema()
            conn = psycopg2.connect(os.environ["DATABASE_URL"])
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM bot_runtime WHERE id = 1")
            row = cur.fetchone()
            bot = dict(row) if row else {}
            cur.close()
            conn.close()
        except Exception as exc:
            bot = {"error": str(exc)}
    return jsonify(
        {
            "state": load_state(),
            "missions": load_missions(),
            "bot": bot,
            "process_alive": is_bot_process_alive(),
        }
    )


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    if not MISSIONS_PATH.exists():
        save_missions([])
    st = load_state()
    st["status"] = "parado"
    st["pid"] = None
    st["message"] = "Cockpit pronto"
    save_state(st)
    print()
    print("=" * 50)
    print("  ProspecTHOR COCKPIT (local)")
    print(f"  http://127.0.0.1:{PORT}")
    print("  Só no seu PC — não é o painel do cliente")
    print("=" * 50)
    print()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
