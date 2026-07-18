"""Testes de regras de impersonate (sem Flask session)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.users import get_user_by_username, list_users, ensure_schema
from src.audit import log_action, query_logs

ensure_schema()

patrao = get_user_by_username("patrao")
amigo = get_user_by_username("admin")
assert patrao and (patrao.get("role") or "") == "admin"
assert amigo and (amigo.get("role") or "") == "client"
print("users ok", patrao["username"], amigo["username"])

# simula regras de API
def can_impersonate(actor_username, target):
    if (actor_username or "").lower() != "patrao":
        return False
    t_user = (target.get("username") or "").lower()
    t_role = (target.get("role") or "").lower()
    if t_user == "patrao" or t_role == "admin":
        return False
    if not target.get("active"):
        return False
    return True

assert can_impersonate("patrao", amigo)
assert not can_impersonate("admin", amigo)
assert not can_impersonate("patrao", patrao)
print("rules ok")

log_action(
    "impersonate_start",
    user_id=patrao["id"],
    username="patrao (impersonate:admin)",
    details={"target": "admin"},
)
logs = query_logs(action="impersonate_start", limit=3)
assert any("impersonate:admin" in (l.get("username") or "") for l in logs)
print("audit ok", logs[0]["username"])
print("ALL OK")
