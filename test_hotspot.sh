#!/bin/bash
# Teste rápido: rode este script DEPOIS de conectar no hotspot do celular
# Uso: bash test_hotspot.sh
cd "$(dirname "$0")"
echo "== Teste via rede atual =="
echo "-- TCP 5432 (pooler) --"
timeout 8 bash -c 'cat < /dev/null > /dev/tcp/52.45.94.125/5432 && echo TCP_OK || echo TCP_FAIL'
echo "-- psycopg2 no Supabase (10s) --"
timeout 20 venv/bin/python -u -c "
import psycopg2
url = [l.split('=',1)[1].strip() for l in open('.env.supabase.bak').read().splitlines() if l.strip().startswith('DATABASE_URL')][0]
try:
    c = psycopg2.connect(url, connect_timeout=8)
    cur = c.cursor(); cur.execute('SELECT 1;'); print('SUPABASE OK!', cur.fetchone())
    c.close()
except Exception as e:
    print('FALHA:', str(e)[:200].replace(chr(10),' | '))
"
echo "Se der SUPABASE OK: me avisa que eu troco o .env de volta e reinicio o cockpit."
