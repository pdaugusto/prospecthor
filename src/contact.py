"""
contact.py — Contato útil para o cliente (WhatsApp/celular ou Instagram).

Regra do produto:
  lead só conta / só vai pro vendedor se tiver ao menos:
    - celular BR (WhatsApp) — DDD + 9xxxxxxxx, OU
    - Instagram (URL ou @)
  Telefone FIXO sozinho NÃO conta (sem WA e sem IG = sem abordagem real).

  Empresas gigantes (Hapvida, hospitais de rede, etc.) NÃO são lead.
"""

from __future__ import annotations

import re
from typing import Any

_IG_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?",
    re.I,
)
_IG_AT_RE = re.compile(r"@([A-Za-z0-9._]{2,30})")

# redes / hospitais / planos — sem decisor tocável
_GIANT_NAME_RE = re.compile(
    r"\b("
    r"hapvida|notre\s*dame|interm[eé]dica|amil|sulam[eé]rica|"
    r"bradesco\s*sa[uú]de|unimed|prevent\s*senior|porto\s*seguro\s*sa[uú]de|"
    r"rede\s*d['\u2019]?or|dasa|fleury|lavoisier|hermes\s*pardini|"
    r"grupo\s*fleury|diagn[oó]sticos\s*da\s*am[eé]rica|"
    r"hospital\s+(albert\s+einstein|s[ií]rio|s[ií]rio[\-\s]?liban[eê]s|"
    r"o\s*swaldo\s*cruz|s[aã]o\s*lu[ií]z|samaritano|moinhos\s*de\s*vento|"
    r"m[aã]e\s*de\s*deus|ernesto\s*dornelles|divina\s*provid[eê]ncia|"
    r"das\s*cl[ií]nicas|universit[aá]rio|regional|municipal|estadual|"
    r"geral|federal|militar)|"
    r"santa\s*casa|"
    r"einstein|s[ií]rio[\-\s]?liban[eê]s|"
    r"\bupa\b|pronto[\-\s]?socorro\s*(municipal|estadual|geral)|"
    r"prefeitura|secretaria\s+de\s+sa[uú]de|"
    r"universidade\s|faculdade\s+de\s+medicina"
    r")\b",
    re.I,
)


_SOCIAL_SITE_MARKERS = (
    "instagram.com", "facebook.com", "fb.com", "linktr.ee",
    "bio.link", "wa.me", "whatsapp.com", "tiktok.com",
)


def has_own_website(url: Any) -> bool:
    """True se URL é site próprio (não só rede social / vazio)."""
    w = (str(url or "")).strip().lower()
    if not w:
        return False
    return not any(m in w for m in _SOCIAL_SITE_MARKERS)


def normalize_phone(raw: Any) -> str:
    if not raw:
        return ""
    cleaned = re.sub(r"[^\d+]", "", str(raw).strip())
    return cleaned


def has_contact_phone(phone: Any) -> bool:
    """Telefone com dígitos suficientes (fixo ou celular). Preferir is_mobile_phone."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return False
    if digits.startswith("55") and len(digits) >= 12:
        return True
    return len(digits) >= 10


def is_mobile_phone(phone: Any) -> bool:
    """Celular BR (abordável no WhatsApp): DDD + 9 + 8 dígitos."""
    return _looks_mobile(phone)


def is_giant_enterprise(company: dict[str, Any] | None = None, *, name: str = "", category: str = "") -> bool:
    """
    True se parece rede/hospital/plano gigante — não gera lead tocável.
    Hapvida, Unimed, hospitais grandes, UPAs, etc.
    """
    if company is not None:
        name = str(company.get("name") or name or "")
        category = str(company.get("category") or category or "")
    blob_name = name or ""
    if _GIANT_NAME_RE.search(blob_name):
        return True
    # "Hospital X" genérico (não clínica/consultório/fisio)
    if re.search(r"\bhospital\b", blob_name, re.I):
        if not re.search(r"cl[ií]nica|consult[oó]rio|fisio|odont|est[eé]tica", blob_name, re.I):
            return True
    cat = category or ""
    if re.search(r"\b(hospital|upa|pronto[\-\s]?socorro)\b", cat, re.I) and re.search(
        r"hospital|upa|pronto", blob_name, re.I
    ):
        return True
    return False


def extract_instagram(company_or_url: Any = None, *, website: str = "", extra: str = "") -> tuple[str, str]:
    """
    Retorna (instagram_url, instagram_username) a partir de website/texto.
    """
    parts: list[str] = []
    if isinstance(company_or_url, dict):
        parts.append(str(company_or_url.get("website") or ""))
        parts.append(str(company_or_url.get("instagram_url") or ""))
        parts.append(str(company_or_url.get("instagram_username") or ""))
        parts.append(str(company_or_url.get("maps_url") or ""))
    elif company_or_url:
        parts.append(str(company_or_url))
    if website:
        parts.append(website)
    if extra:
        parts.append(extra)

    blob = " ".join(parts)
    m = _IG_RE.search(blob)
    if m:
        user = m.group(1).strip(".")
        if user.lower() in ("p", "reel", "reels", "stories", "explore"):
            return "", ""
        return f"https://www.instagram.com/{user}/", user
    # @user solto
    m2 = _IG_AT_RE.search(blob)
    if m2:
        user = m2.group(1)
        return f"https://www.instagram.com/{user}/", user
    return "", ""


def has_instagram(company: dict[str, Any] | None = None, *, url: str = "", username: str = "") -> bool:
    if url or username:
        return bool(url or username)
    if not company:
        return False
    u = (company.get("instagram_url") or "").strip()
    n = (company.get("instagram_username") or "").strip()
    if u or n:
        return True
    ig_u, ig_n = extract_instagram(company)
    return bool(ig_u or ig_n)


def has_usable_contact(company: dict[str, Any] | None = None, *, phone: str = "", instagram_url: str = "") -> bool:
    """
    True se dá pra abordar de verdade:
      - celular (WhatsApp), OU
      - Instagram
    Fixo sozinho NÃO serve. Gigante (Hapvida/hospital rede) NÃO serve.
    """
    if company is not None:
        if is_giant_enterprise(company):
            return False
        if is_mobile_phone(company.get("phone")):
            return True
        if has_instagram(company):
            return True
        ig_u, _ = extract_instagram(company)
        return bool(ig_u)
    if is_mobile_phone(phone):
        return True
    return bool(instagram_url)


def enrich_contact_fields(company: dict[str, Any]) -> dict[str, Any]:
    """
    Preenche instagram_url/username se website (ou outros campos) forem IG.
    Não apaga telefone.
    """
    if not company:
        return company
    ig_u, ig_n = extract_instagram(company)
    if ig_u and not (company.get("instagram_url") or "").strip():
        company["instagram_url"] = ig_u
    if ig_n and not (company.get("instagram_username") or "").strip():
        company["instagram_username"] = ig_n
    # status leve — só WA (celular) ou IG contam como canal útil
    if is_mobile_phone(company.get("phone")):
        company["contact_channel"] = "whatsapp"
    elif company.get("instagram_url") or company.get("instagram_username"):
        company["contact_channel"] = "instagram"
    elif has_contact_phone(company.get("phone")):
        company["contact_channel"] = "phone"  # fixo: fraco, não vira lead
    else:
        company["contact_channel"] = "none"
    return company


def _looks_mobile(phone: Any) -> bool:
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("55"):
        digits = digits[2:]
    # BR celular: 11 dígitos DDD + 9xxxxxxxx
    return len(digits) >= 11 and digits[2:3] == "9"


def primary_contact_channel(company: dict[str, Any]) -> str:
    """
    Canal principal no painel:
      whatsapp | instagram | phone | none
    """
    enrich_contact_fields(company)
    if is_mobile_phone(company.get("phone")):
        return "whatsapp"
    if has_instagram(company):
        return "instagram"
    if has_contact_phone(company.get("phone")):
        return "phone"
    return "none"
