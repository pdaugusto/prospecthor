"""Testes das fases 1-3: active, audit, bot_status."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.users import ensure_schema as users_schema, list_users, update_user, get_user_by_id
from src.audit import ensure_schema as audit_schema, log_action, query_logs
from src.bot_status import (
    ensure_schema as bot_schema,
    set_status,
    add_log,
    increment_session_leads,
    get_status,
)


def main() -> None:
    print("=== schema ===")
    users_schema()
    audit_schema()
    bot_schema()
    print("ok")

    print("=== users / ATIVO ===")
    users = list_users()
    admin = next((u for u in users if u["username"] == "admin"), None)
    assert admin, "admin não existe"
    update_user(admin["id"], active=0)
    a = get_user_by_id(admin["id"])
    assert a and int(a["active"]) == 0, a
    update_user(admin["id"], active=1)
    a = get_user_by_id(admin["id"])
    assert a and int(a["active"]) == 1, a
    print("toggle active ok", a["username"], a["active"])

    print("=== audit ===")
    log_action(
        "test_action",
        user_id=3,
        username="patrao",
        lead_id=1,
        company_name="Empresa Teste",
        details={"foo": "bar"},
    )
    logs = query_logs(username="patrao", limit=5)
    assert logs and logs[0]["action"] == "test_action", logs[:1]
    print("audit ok", logs[0]["action"], logs[0]["username"])

    print("=== bot status ===")
    set_status("rodando", last_job="teste odontologia Campinas", session_leads=0)
    add_log("Iniciando teste de status")
    increment_session_leads(3)
    add_log("3 leads de teste")
    set_status("parado", last_leads=3, session_leads=3, last_job="teste ok")
    st = get_status(10)
    assert st.get("status") == "parado", st
    assert (st.get("last_leads_count") or 0) >= 3 or (st.get("session_leads_count") or 0) >= 3
    assert st.get("logs"), st
    print("bot status ok", st.get("status"), "logs", len(st.get("logs") or []))

    # restaura admin ativo
    update_user(admin["id"], active=1)
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
