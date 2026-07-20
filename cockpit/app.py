"""
Cockpit local do Patrão — opera o bot sem CMD.

  .\venv\Scripts\python.exe cockpit\app.py
  ou: Abrir-Cockpit.bat

http://127.0.0.1:5055
"""
from __future__ import annotations

import json
import os
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
    return _load_json(
        STATE_PATH,
        {
            "status": "parado",
            "message": "Cockpit pronto",
            "pid": None,
            "current_mission_id": None,
            "log": [],
        },
    )


def save_state(state: dict) -> None:
    _save_json(STATE_PATH, state)


def append_log(msg: str, level: str = "INFO") -> None:
    st = load_state()
    lines = list(st.get("log") or [])
    lines.append({"t": _now(), "level": level, "msg": str(msg)[:500]})
    st["log"] = lines[-80:]
    st["message"] = str(msg)[:200]
    save_state(st)
    try:
        from src.bot_status import add_log

        add_log(f"[cockpit] {msg}", level=level)
    except Exception:
        pass


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
    missions = [m for m in load_missions() if m.get("id") != mid]
    save_missions(missions)
    append_log(f"Missão removida: {mid}")
    return jsonify({"ok": True, "missions": missions})


@app.post("/api/missions/clear-done")
def api_clear_done():
    missions = [m for m in load_missions() if m.get("status") in ("pendente", "rodando")]
    save_missions(missions)
    return jsonify({"ok": True, "missions": missions})


def _kill_proc() -> None:
    global _proc
    if _proc is None:
        return
    pid = _proc.pid
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            _proc.terminate()
        except Exception:
            pass
    _proc = None


def _run_one_mission(mission: dict) -> int:
    """Roda main.py run com plano da missão. Retorna exit code."""
    global _proc
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
        f"▶ Missão {mid}: {mission.get('label')} | "
        f"meta={mission.get('target_leads')} | "
        f"nichos={','.join(mission.get('niche_ids') or [])} | "
        f"cidades={len(mission.get('city_ids') or [])} | "
        f"dono={mission.get('username') or 'livre'}"
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

    _proc = subprocess.Popen(
        [python_exe(), str(ROOT / "main.py"), "run"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation,
    )
    st = load_state()
    st["status"] = "rodando"
    st["pid"] = _proc.pid
    st["current_mission_id"] = mid
    save_state(st)

    while _proc.poll() is None:
        if _stop_flag.is_set():
            append_log("Parada pedida — encerrando processo do bot", "WARN")
            _kill_proc()
            return -1
        time.sleep(1.0)

    code = _proc.returncode if _proc else -1
    _proc = None
    return int(code if code is not None else -1)


def _queue_worker() -> None:
    global _worker, _proc
    try:
        while not _stop_flag.is_set():
            missions = load_missions()
            pending = [m for m in missions if m.get("status") == "pendente"]
            if not pending:
                append_log("Fila vazia — cockpit em espera")
                break
            m = pending[0]
            # marca rodando
            for x in missions:
                if x.get("id") == m.get("id"):
                    x["status"] = "rodando"
            save_missions(missions)

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
            append_log(f"Missão {m.get('id')} finalizada (code={code})")

        st = load_state()
        st["status"] = "parado"
        st["pid"] = None
        st["current_mission_id"] = None
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


@app.post("/api/run")
def api_run():
    global _worker
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
        pending = [m for m in load_missions() if m.get("status") == "pendente"]
        if not pending:
            # se tem missão "parada"/"erro", oferece dica
            all_m = load_missions()
            if all_m and not any(m.get("status") == "pendente" for m in all_m):
                return jsonify(
                    {
                        "error": "Nenhuma missão PENDENTE na fila. "
                        "Adicione de novo ou as que tem já estão ok/parada/erro.",
                        "code": "no_pending",
                    }
                ), 400
            return jsonify({"error": "Fila vazia. Adicione uma missão.", "code": "empty"}), 400
        _stop_flag.clear()
        st = load_state()
        st["status"] = "rodando"
        st["message"] = "Iniciando fila…"
        save_state(st)
        _worker = threading.Thread(target=_queue_worker, daemon=True)
        _worker.start()
        append_log(f"Fila iniciada: {len(pending)} missão(ões)")
    return jsonify({"ok": True, "state": load_state()})


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
            append_log("Score: iniciando…")
            from src.scorer import LeadScorer

            scorer = LeadScorer()
            n1 = len(scorer.score_all(force=False) or [])
            append_log(f"Score pendentes: {n1}")
            if force:
                n2 = len(scorer.score_all(force=True) or [])
                append_log(f"Re-score force: {n2}")
            st = load_state()
            st["message"] = f"Score ok ({n1} pendentes)"
            save_state(st)
            append_log("Score finalizado")
        except Exception as exc:
            append_log(f"Score erro: {exc}", "ERROR")

    threading.Thread(target=_job, daemon=True).start()
    return jsonify({"ok": True, "message": "Score em andamento (veja o log)"})


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
