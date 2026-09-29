#!/bin/bash
# ProspecTHOR — Site do cliente local (equivale a `python api/index.py`)
# Acesso: http://localhost:5000  (usuário patrão)
cd "$(dirname "$0")"
echo ""
echo "  ProspecTHOR Site local"
echo "  http://localhost:5000"
echo ""
if [ -x "venv/bin/python" ]; then
  exec venv/bin/python api/index.py
else
  exec python3 api/index.py
fi
