"""
Pontua no Supabase tudo que ainda não tem score.

Use quando o bot parou no meio e sobraram empresas sem classificação.

  python scripts/score_pending.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(ROOT / ".env")

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}", level="INFO")


def main() -> None:
    from src.scorer import LeadScorer

    scorer = LeadScorer()
    leads = scorer.score_all()
    raios = [l for l in leads if l.get("lead_class") == "raio"]
    print()
    print(f"Pontuadas agora: {len(leads)}")
    print(f"Raio (sem site): {len(raios)}")
    for l in raios[:15]:
        print(f"  - {l.get('name')} | {l.get('city')} | score={l.get('lead_score')}")
    if len(raios) > 15:
        print(f"  ... e mais {len(raios) - 15}")
    print()
    print("Atualize o dashboard (F5) para ver os leads.")


if __name__ == "__main__":
    main()
