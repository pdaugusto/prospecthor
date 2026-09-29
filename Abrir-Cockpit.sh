#!/bin/bash
# ProspecTHOR Cockpit — launcher Linux (equivale ao Abrir-Cockpit.bat do Windows)
# Uso: ./Abrir-Cockpit.sh  ou  bash Abrir-Cockpit.sh
cd "$(dirname "$0")"
echo ""
echo "  ProspecTHOR Cockpit"
echo "  http://127.0.0.1:5055"
echo "  Não feche esta janela enquanto usar o painel."
echo ""
if [ -x "venv/bin/python" ]; then
  exec venv/bin/python cockpit/start.py
else
  exec python3 cockpit/start.py
fi
