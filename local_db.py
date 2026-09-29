"""Banco Postgres local (pgserver) — uso offline do cockpit.

Roda em localhost:54321 com dados em ./data/pgdata.
Manter rodando: venv/bin/python local_db.py
A URI é impressa no log e salva em .env.local_db
"""
import time
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parent
PGDATA = ROOT / "data" / "pgdata"
PGDATA.parent.mkdir(parents=True, exist_ok=True)

srv = pgserver.get_server(str(PGDATA), cleanup_mode=None)
uri = srv.get_uri()
print(f"LOCAL_DB_URI={uri}", flush=True)

# garante database postgres existe (get_uri já aponta p/ postgres)
env_file = ROOT / ".env.local_db"
env_file.write_text(f"DATABASE_URL={uri}\n", encoding="utf-8")
print(f"URI salva em {env_file}", flush=True)

try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
