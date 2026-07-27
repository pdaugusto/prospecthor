import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
db_url = os.getenv("DATABASE_URL")
conn = psycopg2.connect(db_url)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Ver users
cur.execute("SELECT id, username, trovoedas_balance, email FROM app_users ORDER BY id DESC LIMIT 5;")
users = cur.fetchall()
print("USERS:")
for u in users:
    print(dict(u))

# Ver ledger
cur.execute("SELECT * FROM trovoeda_ledger ORDER BY id DESC LIMIT 10;")
ledger = cur.fetchall()
print("\nLEDGER:")
for l in ledger:
    print(dict(l))

cur.close()
conn.close()
