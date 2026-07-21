"""Sobe o cockpit e abre o navegador. Usado pelo atalho .bat"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = 5055
URL = f"http://127.0.0.1:{PORT}"


def port_open(host: str = "127.0.0.1", port: int = PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def open_browser_when_ready() -> None:
    for _ in range(40):
        if port_open():
            webbrowser.open(URL)
            return
        time.sleep(0.25)
    webbrowser.open(URL)


def main() -> None:
    if port_open():
        # ja esta rodando — so abre o browser
        webbrowser.open(URL)
        return

    threading.Thread(target=open_browser_when_ready, daemon=True).start()

    # importa e sobe Flask
    from cockpit.app import app  # noqa: WPS433

    print()
    print("  ProspecTHOR COCKPIT")
    print(f"  {URL}")
    print("  Nao feche esta janela enquanto usar o painel.")
    print()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
