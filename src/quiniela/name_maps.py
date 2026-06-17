from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict

from unidecode import unidecode


@dataclass(frozen=True)
class TeamSpec:
    team_norm: str
    preferred_search_name: str
    aliases: tuple[str, ...]


class UnknownTeamNameError(ValueError):
    def __init__(self, value: str, *, context: str | None = None) -> None:
        detail = f"Selección desconocida: {value!r}"
        if context:
            detail = f"{detail} ({context})"
        super().__init__(detail)
        self.value = value
        self.context = context


TEAM_SPECS: tuple[TeamSpec, ...] = (
    TeamSpec("mexico", "Mexico", ("Mexico", "MÃ©xico")),
    TeamSpec("south_africa", "South Africa", ("South Africa", "SudÃ¡frica")),
    TeamSpec(
        "south_korea",
        "South Korea",
        (
            "South Korea",
            "Korea Republic",
            "Republic of Korea",
            "Corea del Sur",
            "RepÃºblica de Corea",
        ),
    ),
    TeamSpec(
        "czech_republic",
        "Czech Republic",
        ("Czech Republic", "Czechia", "Chequia", "RepÃºblica Checa"),
    ),
    TeamSpec("canada", "Canada", ("Canada", "CanadÃ¡")),
    TeamSpec("qatar", "Qatar", ("Qatar", "Catar")),
    TeamSpec("switzerland", "Switzerland", ("Switzerland", "Suiza")),
    TeamSpec("sweden", "Sweden", ("Sweden", "Suecia")),
    TeamSpec(
        "bosnia_herzegovina",
        "Bosnia & Herzegovina",
        ("Bosnia & Herzegovina", "Bosnia y Herzegovina"),
    ),
    TeamSpec("usa", "United States", ("United States", "USA", "Estados Unidos")),
    TeamSpec("germany", "Germany", ("Germany", "Alemania")),
    TeamSpec("argentina", "Argentina", ("Argentina",)),
    TeamSpec("austria", "Austria", ("Austria",)),
    TeamSpec("paraguay", "Paraguay", ("Paraguay",)),
    TeamSpec("brazil", "Brazil", ("Brazil", "Brasil")),
    TeamSpec("belgium", "Belgium", ("Belgium", "BÃ©lgica")),
    TeamSpec("algeria", "Algeria", ("Algeria", "Argelia")),
    TeamSpec("colombia", "Colombia", ("Colombia",)),
    TeamSpec("croatia", "Croatia", ("Croatia", "Croacia")),
    TeamSpec("ecuador", "Ecuador", ("Ecuador",)),
    TeamSpec("egypt", "Egypt", ("Egypt", "Egipto")),
    TeamSpec("spain", "Spain", ("Spain", "EspaÃ±a")),
    TeamSpec("france", "France", ("France", "Francia")),
    TeamSpec("ghana", "Ghana", ("Ghana",)),
    TeamSpec("england", "England", ("England", "Inglaterra")),
    TeamSpec("iran", "Iran", ("Iran", "IrÃ¡n", "RI de IrÃ¡n", "IR Iran")),
    TeamSpec("iraq", "Iraq", ("Iraq", "Irak")),
    TeamSpec("japan", "Japan", ("Japan", "JapÃ³n")),
    TeamSpec("jordan", "Jordan", ("Jordan", "Jordania")),
    TeamSpec(
        "ivory_coast",
        "Ivory Coast",
        ("Ivory Coast", "Cote d'Ivoire", "CÃ´te d'Ivoire", "Costa de Marfil"),
    ),
    TeamSpec("netherlands", "Netherlands", ("Netherlands", "PaÃ­ses Bajos")),
    TeamSpec("curacao", "Curacao", ("Curacao", "CuraÃ§ao", "Curazao")),
    TeamSpec("norway", "Norway", ("Norway", "Noruega")),
    TeamSpec("new_zealand", "New Zealand", ("New Zealand", "Nueva Zelanda")),
    TeamSpec("portugal", "Portugal", ("Portugal",)),
    TeamSpec("senegal", "Senegal", ("Senegal",)),
    TeamSpec("tunisia", "Tunisia", ("Tunisia", "TÃºnez")),
    TeamSpec("turkey", "Turkey", ("Turkey", "TurquÃ­a")),
    TeamSpec("australia", "Australia", ("Australia",)),
    TeamSpec("morocco", "Morocco", ("Morocco", "Marruecos")),
    TeamSpec("haiti", "Haiti", ("Haiti", "HaitÃ­")),
    TeamSpec("scotland", "Scotland", ("Scotland", "Escocia")),
    TeamSpec(
        "saudi_arabia",
        "Saudi Arabia",
        ("Saudi Arabia", "Arabia SaudÃ­", "Arabia Saudita"),
    ),
    TeamSpec("cape_verde", "Cape Verde", ("Cape Verde", "Cabo Verde")),
    TeamSpec(
        "dr_congo",
        "DR Congo",
        ("DR Congo", "RD Congo", "Congo DR", "Democratic Republic of the Congo"),
    ),
    TeamSpec("panama", "Panama", ("Panama", "PanamÃ¡")),
    TeamSpec("uruguay", "Uruguay", ("Uruguay",)),
    TeamSpec("uzbekistan", "Uzbekistan", ("Uzbekistan", "UzbekistÃ¡n")),
)


TEAM_ALIASES: Dict[str, tuple[str, ...]] = {
    spec.team_norm: spec.aliases for spec in TEAM_SPECS
}
TEAM_NAME_MAP: Dict[str, str] = {}
TEAM_SEARCH_PREFERENCE: Dict[str, str] = {
    spec.team_norm: spec.preferred_search_name for spec in TEAM_SPECS
}


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


def _question_mark_variant(value: str) -> str:
    return "".join("?" if ord(char) > 127 else char for char in value)


def _alias_variants(alias: str) -> set[str]:
    repaired = fix_common_mojibake(alias)
    variants = {
        alias.strip(),
        repaired.strip(),
        unidecode(repaired).strip(),
    }
    if any(ord(char) > 127 for char in repaired):
        variants.add(_question_mark_variant(repaired).strip())
    return {variant for variant in variants if variant}


for spec in TEAM_SPECS:
    for alias in spec.aliases:
        for variant in _alias_variants(alias):
            TEAM_NAME_MAP[variant] = spec.team_norm


NORMALIZED_TEAM_MAP = {
    normalize_text(alias): team_norm
    for alias, team_norm in TEAM_NAME_MAP.items()
}
KNOWN_TEAM_NORMS = frozenset(spec.team_norm for spec in TEAM_SPECS)


def normalize_team_name(
    value: str,
    *,
    strict: bool = False,
    context: str | None = None,
) -> str:
    stripped = fix_common_mojibake((value or "").strip())
    if stripped in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[stripped]

    ascii_key = unidecode(stripped).strip()
    if ascii_key in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[ascii_key]

    normalized = normalize_text(stripped)
    if normalized in NORMALIZED_TEAM_MAP:
        return NORMALIZED_TEAM_MAP[normalized]

    if strict:
        raise UnknownTeamNameError(stripped or value, context=context)
    return normalized


def require_known_team_name(value: str, *, context: str | None = None) -> str:
    return normalize_team_name(value, strict=True, context=context)


def is_known_team_name(value: str) -> bool:
    try:
        return normalize_team_name(value, strict=True) in KNOWN_TEAM_NORMS
    except UnknownTeamNameError:
        return False


def preferred_team_search_name(value: str) -> str:
    team_norm = normalize_team_name(value)
    return TEAM_SEARCH_PREFERENCE.get(team_norm, fix_common_mojibake(value).strip())
