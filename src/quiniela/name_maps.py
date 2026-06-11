from __future__ import annotations

import re
from typing import Dict

from rapidfuzz import fuzz, process
from unidecode import unidecode


TEAM_NAME_MAP: Dict[str, str] = {
    "México": "mexico",
    "MÃ©xico": "mexico",
    "Mexico": "mexico",
    "M?xico": "mexico",
    "Sudáfrica": "south_africa",
    "SudÃ¡frica": "south_africa",
    "Sudafrica": "south_africa",
    "Sud?frica": "south_africa",
    "South Africa": "south_africa",
    "República de Corea": "south_korea",
    "RepÃºblica de Corea": "south_korea",
    "Republica de Corea": "south_korea",
    "Rep?blica de Corea": "south_korea",
    "Corea del Sur": "south_korea",
    "South Korea": "south_korea",
    "Korea Republic": "south_korea",
    "Republic of Korea": "south_korea",
    "Chequia": "czech_republic",
    "República Checa": "czech_republic",
    "RepÃºblica Checa": "czech_republic",
    "Republica Checa": "czech_republic",
    "Rep?blica Checa": "czech_republic",
    "Czech Republic": "czech_republic",
    "Canadá": "canada",
    "CanadÃ¡": "canada",
    "Canada": "canada",
    "Catar": "qatar",
    "Qatar": "qatar",
    "Bosnia y Herzegovina": "bosnia_herzegovina",
    "Bosnia & Herzegovina": "bosnia_herzegovina",
    "Estados Unidos": "usa",
    "USA": "usa",
    "United States": "usa",
    "Paraguay": "paraguay",
    "Argelia": "algeria",
    "Algeria": "algeria",
    "Irán": "iran",
    "IrÃ¡n": "iran",
    "Iran": "iran",
    "RI de Irán": "iran",
    "RI de IrÃ¡n": "iran",
    "Costa de Marfil": "ivory_coast",
    "Ivory Coast": "ivory_coast",
    "Países Bajos": "netherlands",
    "PaÃ­ses Bajos": "netherlands",
    "Netherlands": "netherlands",
    "Curazao": "curacao",
    "Curacao": "curacao",
    "Túnez": "tunisia",
    "TÃºnez": "tunisia",
    "Tunisia": "tunisia",
    "Turquía": "turkey",
    "TurquÃ­a": "turkey",
    "Turkey": "turkey",
    "Marruecos": "morocco",
    "Morocco": "morocco",
    "Arabia Saudí": "saudi_arabia",
    "Arabia SaudÃ­": "saudi_arabia",
    "Arabia Saudita": "saudi_arabia",
    "Saudi Arabia": "saudi_arabia",
    "Cabo Verde": "cape_verde",
    "Cape Verde": "cape_verde",
    "RD Congo": "dr_congo",
    "DR Congo": "dr_congo",
    "República Democrática del Congo": "dr_congo",
    "RepÃºblica DemocrÃ¡tica del Congo": "dr_congo",
    "Panamá": "panama",
    "PanamÃ¡": "panama",
    "Panama": "panama",
}

TEAM_SEARCH_PREFERENCE: Dict[str, str] = {
    "mexico": "Mexico",
    "south_africa": "South Africa",
    "south_korea": "South Korea",
    "czech_republic": "Czech Republic",
    "canada": "Canada",
    "usa": "United States",
    "paraguay": "Paraguay",
    "qatar": "Qatar",
    "bosnia_herzegovina": "Bosnia",
    "algeria": "Algeria",
    "iran": "Iran",
    "ivory_coast": "Ivory Coast",
    "netherlands": "Netherlands",
    "curacao": "Curacao",
    "tunisia": "Tunisia",
    "turkey": "Turkey",
    "morocco": "Morocco",
    "saudi_arabia": "Saudi Arabia",
    "cape_verde": "Cape Verde",
    "dr_congo": "DR Congo",
    "panama": "Panama",
}

NORMALIZED_TEAM_MAP = {unidecode(key).lower(): value for key, value in TEAM_NAME_MAP.items()}


def fix_common_mojibake(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return text
    try:
        repaired = text.encode("latin1").decode("utf-8")
        if repaired and repaired != text:
            return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return text


def normalize_text(value: str) -> str:
    text = unidecode(fix_common_mojibake((value or "").strip())).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    text = re.sub(r"[-\s]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def normalize_team_name(value: str) -> str:
    stripped = fix_common_mojibake((value or "").strip())
    if stripped in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[stripped]

    ascii_key = unidecode(stripped).lower()
    if ascii_key in NORMALIZED_TEAM_MAP:
        return NORMALIZED_TEAM_MAP[ascii_key]

    normalized = normalize_text(stripped)
    if normalized in NORMALIZED_TEAM_MAP:
        return NORMALIZED_TEAM_MAP[normalized]

    if normalized:
        compact = normalized.replace("_", "")
        choices = {normalize_text(key).replace("_", ""): norm for key, norm in TEAM_NAME_MAP.items()}
        match = process.extractOne(compact, list(choices.keys()), scorer=fuzz.ratio, score_cutoff=72)
        if match:
            return choices[match[0]]
    return normalized


def preferred_team_search_name(value: str) -> str:
    team_norm = normalize_team_name(value)
    return TEAM_SEARCH_PREFERENCE.get(team_norm, fix_common_mojibake(value).strip())
