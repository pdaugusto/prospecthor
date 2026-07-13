"""
settings.py - Configurações centrais do Prospector Bot
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis do .env
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """Configurações centralizadas do bot."""

    # ── Diretórios ──────────────────────────────────────
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    EXPORT_DIR: Path = BASE_DIR / "data" / "exports"

    # ── Banco de dados ──────────────────────────────────
    DATABASE_PATH: str = str(DATA_DIR / "leads.db")

    # ── Google Maps / Places API ────────────────────────
    GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
    USE_SCRAPING_FALLBACK: bool = os.getenv("USE_SCRAPING_FALLBACK", "true").lower() == "true"
    MAX_RESULTS_PER_QUERY: int = int(os.getenv("MAX_RESULTS_PER_SEARCH", "60"))

    # ── Playwright / Scraping ───────────────────────────
    PLAYWRIGHT_HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    PLAYWRIGHT_TIMEOUT_MS: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))
    REQUEST_DELAY_MIN_S: float = float(os.getenv("REQUEST_DELAY_MIN_S", "1.0"))
    REQUEST_DELAY_MAX_S: float = float(os.getenv("REQUEST_DELAY_MAX_S", "3.5"))

    # ── Scorer ──────────────────────────────────────────
    MIN_SCORE_TO_SAVE: int = int(os.getenv("MIN_SCORE_TO_SAVE", "21"))
    NOTIFY_MIN_SCORE: int = int(os.getenv("NOTIFY_MIN_SCORE", "46"))

    # ── Dashboard ───────────────────────────────────────
    DASHBOARD_USER: str = os.getenv("DASHBOARD_USER", "admin")
    DASHBOARD_PASS: str = os.getenv("DASHBOARD_PASS", "senha123")
    DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", "5000"))
    DASHBOARD_SECRET_KEY: str = os.getenv("DASHBOARD_SECRET_KEY", "mudar-essa-chave")

    # ── Agendamento ─────────────────────────────────────
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    SCHEDULE_MORNING: str = os.getenv("SCHEDULE_MORNING", "08:00")
    SCHEDULE_MIDDAY: str = os.getenv("SCHEDULE_MIDDAY", "10:00")
    SCHEDULE_AFTERNOON: str = os.getenv("SCHEDULE_AFTERNOON", "14:00")
    SCHEDULE_LATE: str = os.getenv("SCHEDULE_LATE", "16:00")
    SCHEDULE_EVENING: str = os.getenv("SCHEDULE_EVENING", "20:00")
    SCHEDULE_REPORT: str = os.getenv("SCHEDULE_REPORT", "22:00")

    # ── Busca padrão ────────────────────────────────────
    SEARCH_CITY: str = os.getenv("SEARCH_CITY", "Porto Alegre")
    SEARCH_STATE: str = os.getenv("SEARCH_STATE", "RS")

    # ── Nichos ──────────────────────────────────────────
    def get_active_niches(self) -> list:
        """Retorna lista de nichos ativos do arquivo niches.json."""
        niches_path = self.BASE_DIR / "config" / "niches.json"
        if niches_path.exists():
            import json
            with open(niches_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "niches" in data:
                    return data["niches"]
                return data if isinstance(data, list) else []
        return []

    # ── Cidades ─────────────────────────────────────────
    def get_active_cities(self) -> list:
        """Retorna lista de cidades ativas do arquivo cities.json."""
        cities_path = self.BASE_DIR / "config" / "cities.json"
        if cities_path.exists():
            import json
            with open(cities_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "cities" in data:
                    return data["cities"]
                return data if isinstance(data, list) else []
        return []

    def __repr__(self):
        return f"<Settings db={self.DATABASE_PATH} city={self.SEARCH_CITY}>"


# Instância singleton — todos os módulos importam daqui
settings = Settings()

# Garante que diretórios existam
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.EXPORT_DIR.mkdir(parents=True, exist_ok=True)