from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection
from quiniela.name_maps import normalize_team_name


POSITION_MAP = {
    "G": "POR",
    "D": "DEF",
    "M": "MED",
    "F": "DEL",
}


@dataclass(frozen=True)
class LineupPlayer:
    name: str
    number: str
    position: str


def _position_label(position_code: str | None) -> str:
    if not position_code:
        return "-"
    return POSITION_MAP.get(str(position_code).upper().strip(), str(position_code).upper().strip())


def extract_lineup_players(payload: dict[str, Any], section: str) -> list[LineupPlayer]:
    players: list[LineupPlayer] = []
    for row in payload.get(section) or []:
        player = row.get("player", {})
        players.append(
            LineupPlayer(
                name=str(player.get("name") or "Sin nombre"),
                number=str(player.get("number") or "-"),
                position=_position_label(player.get("pos")),
            )
        )
    return players


def _render_player_list(players: list[LineupPlayer], empty_text: str) -> str:
    if not players:
        return f'<p class="lineup-empty">{escape(empty_text)}</p>'
    items = []
    for player in players:
        items.append(
            "<li>"
            f'<span class="player-number">{escape(player.number)}</span>'
            f'<span class="player-name">{escape(player.name)}</span>'
            f'<span class="player-position">{escape(player.position)}</span>'
            "</li>"
        )
    return f'<ol class="player-list">{"".join(items)}</ol>'


def _render_team_lineup(team_name: str, lineup_payload: dict[str, Any] | None) -> str:
    if lineup_payload is None:
        return (
            '<section class="lineup-team">'
            f"<h4>{escape(team_name)}</h4>"
            '<p class="lineup-empty">Alineación no confirmada todavía.</p>'
            "</section>"
        )

    starters = extract_lineup_players(lineup_payload, "startXI")
    substitutes = extract_lineup_players(lineup_payload, "substitutes")
    formation = lineup_payload.get("formation") or "Pendiente"
    coach = lineup_payload.get("coach", {}).get("name") or "Pendiente"
    source_kind = lineup_payload.get("_lineup_source") or "official"
    source_html = ""
    if source_kind == "web_estimated":
        confidence = float(lineup_payload.get("_confidence") or 0.0)
        source_links = []
        for index, source in enumerate((lineup_payload.get("_sources") or [])[:3], start=1):
            url = str(source.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            source_links.append(
                f'<a href="{escape(url, quote=True)}" target="_blank" rel="noreferrer">'
                f"Fuente {index}</a>"
            )
        links_html = " · ".join(source_links) if source_links else "Sin enlaces conservados"
        source_html = (
            '<div class="lineup-estimate-note">'
            f"<strong>Alineación estimada por noticias · confianza {confidence * 100:.0f}%</strong>"
            f"<span>{links_html}</span>"
            "</div>"
        )
    return (
        '<section class="lineup-team">'
        f"<h4>{escape(team_name)}</h4>"
        f"{source_html}"
        '<div class="lineup-meta">'
        f"<span>Formación: {escape(str(formation))}</span>"
        f"<span>DT: {escape(str(coach))}</span>"
        "</div>"
        '<div class="lineup-columns">'
        '<div>'
        "<h5>Titulares</h5>"
        f'{_render_player_list(starters, "Sin titulares confirmados.")}'
        "</div>"
        '<div>'
        "<h5>En banca</h5>"
        f'{_render_player_list(substitutes, "Sin suplentes confirmados.")}'
        "</div>"
        "</div>"
        "</section>"
    )


def _load_lineups_by_fixture(connection: sqlite3.Connection, fixture_ids: list[str]) -> dict[tuple[str, str], dict[str, Any]]:
    if not fixture_ids:
        return {}
    placeholders = ",".join("?" for _ in fixture_ids)
    rows = connection.execute(
        f"""
        SELECT fixture_id, team_norm, source_json
        FROM historical_lineups
        WHERE fixture_id IN ({placeholders})
        ORDER BY id DESC
        """,
        fixture_ids,
    ).fetchall()
    lineups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["fixture_id"]), str(row["team_norm"]))
        if key not in lineups:
            payload = json.loads(row["source_json"])
            if len(payload.get("startXI") or []) >= 11:
                payload["_lineup_source"] = "official"
                lineups[key] = payload
    estimate_rows = connection.execute(
        f"""
        SELECT fixture_id, team_norm, source_json
        FROM lineup_estimates
        WHERE fixture_id IN ({placeholders})
        ORDER BY id DESC
        """,
        fixture_ids,
    ).fetchall()
    for row in estimate_rows:
        key = (str(row["fixture_id"]), str(row["team_norm"]))
        if key not in lineups:
            lineups[key] = json.loads(row["source_json"])
    return lineups


def _load_prediction_impacts(
    connection: sqlite3.Connection,
    match_ids: list[str],
    prediction_ids: list[int] | None = None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if not match_ids:
        return {}
    placeholders = ",".join("?" for _ in match_ids)
    params: list[Any] = list(match_ids)
    prediction_filter = ""
    if prediction_ids:
        prediction_placeholders = ",".join("?" for _ in prediction_ids)
        prediction_filter = f" AND prediction_id IN ({prediction_placeholders})"
        params.extend(prediction_ids)
    rows = connection.execute(
        f"""
        SELECT match_id, team_norm, source_json
        FROM prediction_player_impacts
        WHERE match_id IN ({placeholders})
        {prediction_filter}
        ORDER BY match_id, net_impact DESC, id ASC
        """,
        params,
    ).fetchall()
    impacts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["match_id"]), str(row["team_norm"]))
        impacts.setdefault(key, []).append(json.loads(row["source_json"]))
    return impacts


def _fill_snapshot_impacts(
    connection: sqlite3.Connection,
    match_rows: list[dict[str, Any]],
    impacts: dict[tuple[str, str], list[dict[str, Any]]],
) -> None:
    for row in match_rows:
        match_id = str(row["match_id"])
        expected_teams = {
            normalize_team_name(str(row["home_team"])),
            normalize_team_name(str(row["away_team"])),
        }
        if all((match_id, team_norm) in impacts for team_norm in expected_teams):
            continue
        snapshot = connection.execute(
            """
            SELECT id
            FROM pre_match_snapshots
            WHERE match_id = ?
              AND julianday(captured_at) < julianday(kickoff_at)
            ORDER BY julianday(captured_at) DESC, id DESC
            LIMIT 1
            """,
            (match_id,),
        ).fetchone()
        if snapshot is None:
            continue
        players = connection.execute(
            """
            SELECT team_norm, features_json
            FROM pre_match_player_snapshots
            WHERE snapshot_id = ?
            ORDER BY net_impact DESC, id
            """,
            (int(snapshot["id"]),),
        ).fetchall()
        for player in players:
            key = (match_id, str(player["team_norm"]))
            impacts.setdefault(key, []).append(json.loads(player["features_json"]))


def _render_impact_summary(impacts: list[dict[str, Any]]) -> str:
    if not impacts:
        return '<p class="impact-empty">Sin desglose de impacto disponible todavía.</p>'
    top_rows = sorted(impacts, key=lambda row: float(row.get("net_impact", 0.0)), reverse=True)[:3]
    items = []
    for row in top_rows:
        items.append(
            "<li>"
            f"<strong>{escape(str(row.get('player_name') or 'Sin nombre'))}</strong>"
            f"<span>{escape(str(row.get('role_bucket') or 'unknown'))}</span>"
            f"<em>{float(row.get('net_impact', 0.0)):.2f}</em>"
            "</li>"
        )
    attack = sum(float(row.get("attack_impact", 0.0)) for row in top_rows)
    defense = sum(float(row.get("defense_impact", 0.0)) for row in top_rows)
    discipline = sum(float(row.get("discipline_impact", 0.0)) for row in top_rows)
    return (
        '<div class="impact-box">'
        "<h5>Top impactos</h5>"
        f'<ol class="impact-list">{"".join(items)}</ol>'
        '<div class="impact-axis">'
        f"<span>Ataque: {attack:.2f}</span>"
        f"<span>Defensa/control: {defense:.2f}</span>"
        f"<span>Disciplina: {discipline:.2f}</span>"
        "</div>"
        "</div>"
    )


def _load_match_rows(connection: sqlite3.Connection, dates: list[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in dates)
    query = f"""
        WITH latest_predictions AS (
            SELECT p.*
            FROM predictions p
            INNER JOIN (
                SELECT match_id, MAX(id) AS max_id
                FROM predictions
                GROUP BY match_id
            ) latest
            ON p.match_id = latest.match_id AND p.id = latest.max_id
        )
        SELECT
            m.match_id,
            m.date_cdmx,
            m.time_cdmx,
            m.datetime_cdmx,
            m.home_team,
            m.away_team,
            m.home_team_norm,
            m.away_team_norm,
            m.group_name,
            m.stadium,
            m.stage,
            m.status,
            m.api_fixture_id,
            lp.predicted_score,
            lp.probability,
            lp.model_version,
            lp.hybrid_predicted_score,
            lp.hybrid_probability,
            lp.home_win_probability,
            lp.draw_probability,
            lp.away_win_probability,
            lp.outcome_model_version,
            lp.data_freshness_at,
            lp.generated_at,
            json_extract(lp.source_json, '$.odds_consensus') AS odds_consensus,
            json_extract(lp.source_json, '$.odds_adjusted_exact_score') AS odds_adjusted_exact_score,
            json_extract(lp.source_json, '$.odds_adjusted_probability') AS odds_adjusted_probability,
            json_extract(lp.source_json, '$.odds_model_version') AS odds_model_version,
            (
                SELECT release_id FROM model_releases
                WHERE status = 'active'
                ORDER BY activated_at DESC, id DESC
                LIMIT 1
            ) AS evidence_release_id,
            (
                SELECT preliminary FROM model_releases
                WHERE status = 'active'
                ORDER BY activated_at DESC, id DESC
                LIMIT 1
            ) AS evidence_release_preliminary,
            ar.home_goals AS actual_home_goals,
            ar.away_goals AS actual_away_goals
        FROM matches m
        LEFT JOIN latest_predictions lp
            ON lp.match_id = m.match_id
        LEFT JOIN actual_results ar
            ON ar.match_id = m.match_id
        WHERE m.date_cdmx IN ({placeholders})
        ORDER BY m.datetime_cdmx, m.home_team
    """
    df = fetch_dataframe(connection, query, dates)
    return df.to_dict(orient="records")


def _attach_odds_consensus(
    connection: sqlite3.Connection,
    match_rows: list[dict[str, Any]],
) -> None:
    fixture_ids = [
        str(int(row["api_fixture_id"]))
        for row in match_rows
        if row.get("api_fixture_id") not in (None, "")
        and str(row.get("api_fixture_id")) != "nan"
    ]
    if not fixture_ids:
        return
    placeholders = ",".join("?" for _ in fixture_ids)
    rows = connection.execute(
        f"""
        SELECT fixture_id, market_key, selection_key, line_key,
               consensus_probability, median_decimal_odd, bookmaker_count
        FROM odds_market_consensus
        WHERE fixture_id IN ({placeholders})
        ORDER BY fixture_id, market_key, line_key, selection_key
        """,
        fixture_ids,
    ).fetchall()
    by_fixture: dict[str, dict[str, Any]] = {}
    for row in rows:
        fixture_key = str(row["fixture_id"])
        line_key = str(row["line_key"] or "")
        market_key = str(row["market_key"])
        bucket_key = market_key if not line_key else f"{market_key}:{line_key}"
        by_fixture.setdefault(fixture_key, {"fixture_id": fixture_key, "markets": {}})
        by_fixture[fixture_key]["markets"].setdefault(bucket_key, {})[
            str(row["selection_key"])
        ] = {
            "probability": float(row["consensus_probability"]),
            "median_decimal_odd": (
                float(row["median_decimal_odd"])
                if row["median_decimal_odd"] is not None
                else None
            ),
            "bookmakers": int(row["bookmaker_count"] or 0),
        }
    for row in match_rows:
        fixture_id = row.get("api_fixture_id")
        if fixture_id in (None, "") or str(fixture_id) == "nan":
            continue
        fixture_key = str(int(fixture_id))
        if fixture_key in by_fixture:
            row["odds_consensus_full"] = by_fixture[fixture_key]


def _score_comparison(actual_home_goals: Any, actual_away_goals: Any) -> str:
    if actual_home_goals is None or actual_away_goals is None:
        return "Pendiente"
    if str(actual_home_goals) == "nan" or str(actual_away_goals) == "nan":
        return "Pendiente"
    return f"{int(actual_home_goals)}-{int(actual_away_goals)}"


def _format_cdmx_timestamp(value: datetime | None = None) -> str:
    settings = get_settings()
    local_tz = ZoneInfo(settings.local_timezone)
    timestamp = value.astimezone(local_tz) if value else datetime.now(local_tz)
    return f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} CDMX"


def _render_evaluation(match: dict[str, Any]) -> str:
    def available(value: Any) -> bool:
        return value is not None and not (
            isinstance(value, float) and math.isnan(value)
        )

    if not available(match.get("actual_home_goals")) or not available(
        match.get("goal_mae")
    ):
        return '<p class="evaluation-empty">Evaluación pendiente hasta el resultado final.</p>'
    outcome_correct = match.get("outcome_correct")
    outcome_label = (
        "correcto"
        if available(outcome_correct) and int(outcome_correct) == 1
        else "incorrecto"
    )
    return (
        '<div class="evaluation-box">'
        f"<span>MAE goles: <b>{float(match.get('goal_mae') or 0.0):.3f}</b></span>"
        f"<span>Devianza: <b>{float(match.get('poisson_deviance') or 0.0):.3f}</b></span>"
        f"<span>1-X-2: <b>{outcome_label}</b></span>"
        f"<span>Log-loss: <b>{float(match.get('log_loss') or 0.0):.3f}</b></span>"
        f"<span>Brier: <b>{float(match.get('brier_score') or 0.0):.3f}</b></span>"
        "</div>"
    )


def _render_prediction_timeline(match: dict[str, Any]) -> str:
    timeline = match.get("timeline") or []
    if not timeline:
        return '<p class="timeline-empty">Sin revisiones prepartido registradas.</p>'
    rows = []
    for item in timeline:
        canonical = " · canónica" if int(item.get("is_canonical") or 0) else ""
        rows.append(
            "<li>"
            f"<time>{escape(str(item.get('generated_at_utc') or 'n/d'))}</time>"
            f"<strong>{escape(str(item.get('hybrid_predicted_score') or item.get('predicted_score') or 'n/d'))}</strong>"
            f"<span>{escape(str(item.get('prediction_context') or 'legacy'))}"
            f"{escape(canonical)}</span>"
            "</li>"
        )
    return (
        '<details class="timeline-accordion">'
        f"<summary>Historial prepartido ({len(rows)})</summary>"
        f'<ol class="timeline-list">{"".join(rows)}</ol>'
        "</details>"
    )


def _json_field(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value or (isinstance(value, float) and math.isnan(value)):
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _format_probability(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/d"


def _format_decimal_odd(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/d"


def _market_item(
    label: str,
    item: dict[str, Any],
    *,
    tone: str = "blue",
) -> str:
    probability = _format_probability(item.get("probability"))
    odd = _format_decimal_odd(item.get("median_decimal_odd"))
    bookmakers = int(item.get("bookmakers") or 0)
    bookmaker_text = f"{bookmakers} casas" if bookmakers else "consenso"
    return (
        f'<span class="market-tag {escape(tone)}">'
        f"<strong>{escape(label)}</strong>"
        f"<b>{escape(probability)}</b>"
        f"<em>momio {escape(odd)} · {escape(bookmaker_text)}</em>"
        "</span>"
    )


def _top_market_items(
    markets: dict[str, Any],
    market_key: str,
    *,
    limit: int = 3,
) -> list[tuple[str, dict[str, Any]]]:
    values = markets.get(market_key) or {}
    ranked = sorted(
        values.items(),
        key=lambda item: float((item[1] or {}).get("probability") or 0.0),
        reverse=True,
    )
    return [(str(key), dict(value or {})) for key, value in ranked[:limit]]


def _top_market_items_by_prefix(
    markets: dict[str, Any],
    market_prefix: str,
    *,
    limit: int = 3,
) -> list[tuple[str, dict[str, Any]]]:
    merged: dict[str, dict[str, Any]] = {}
    for key, values in markets.items():
        if key != market_prefix and not key.startswith(f"{market_prefix}:"):
            continue
        for selection, item in (values or {}).items():
            current = merged.get(str(selection))
            if current is None or float(item.get("probability") or 0.0) > float(
                current.get("probability") or 0.0
            ):
                merged[str(selection)] = dict(item or {})
    ranked = sorted(
        merged.items(),
        key=lambda item: float((item[1] or {}).get("probability") or 0.0),
        reverse=True,
    )
    return [(str(key), dict(value or {})) for key, value in ranked[:limit]]


def _closest_line_key(
    markets: dict[str, Any],
    prefix: str,
    target: float = 2.5,
) -> str | None:
    keys = [key for key in markets if key.startswith(f"{prefix}:")]
    if not keys:
        return None

    def distance(key: str) -> float:
        try:
            return abs(float(key.split(":", 1)[1]) - target)
        except ValueError:
            return 999.0

    return sorted(keys, key=distance)[0]


def _market_group(
    title: str,
    subtitle: str,
    items: list[str],
) -> str:
    if not items:
        return ""
    return (
        '<section class="market-panel">'
        f"<h5>{escape(title)}</h5>"
        f"<p>{escape(subtitle)}</p>"
        f'<div class="market-tags">{"".join(items)}</div>'
        "</section>"
    )


def _model_market_signal(match: dict[str, Any], match_winner: dict[str, Any]) -> str:
    if not match_winner:
        return '<span class="signal-chip neutral">Sin consenso 1-X-2 todavia</span>'
    model = {
        "1": float(match.get("home_win_probability") or 0.0),
        "X": float(match.get("draw_probability") or 0.0),
        "2": float(match.get("away_win_probability") or 0.0),
    }
    market = {
        "1": float((match_winner.get("home") or {}).get("probability") or 0.0),
        "X": float((match_winner.get("draw") or {}).get("probability") or 0.0),
        "2": float((match_winner.get("away") or {}).get("probability") or 0.0),
    }
    if not any(model.values()) or not any(market.values()):
        return '<span class="signal-chip neutral">Comparacion modelo/mercado pendiente</span>'
    label = max(model, key=lambda key: abs(model[key] - market[key]))
    delta = model[label] - market[label]
    strength = "alta" if abs(delta) >= 0.12 else "media" if abs(delta) >= 0.07 else "baja"
    direction = "modelo arriba" if delta > 0 else "mercado arriba"
    return (
        f'<span class="signal-chip {escape(strength)}">'
        f"senal {escape(strength)} en {escape(label)}: {escape(direction)} "
        f"{abs(delta) * 100:.1f} pts"
        "</span>"
    )


def _render_market_section(match: dict[str, Any]) -> str:
    consensus = _json_field(match.get("odds_consensus_full")) or _json_field(match.get("odds_consensus"))
    markets = consensus.get("markets") or {}
    exact_score = match.get("odds_adjusted_exact_score")
    exact_probability = match.get("odds_adjusted_probability")
    if not markets and not exact_score:
        return '<p class="market-empty">Momios prepartido pendientes para este partido.</p>'

    match_winner = markets.get("match_winner") or {}
    top_exact_scores = _top_market_items_by_prefix(markets, "exact_score", limit=3)
    headline_tags = []
    if exact_score:
        headline_tags.append(
            '<span class="market-tag gold featured">'
            "<strong>Marcador odds-aware</strong>"
            f"<b>{escape(str(exact_score))}</b>"
            f"<em>{escape(_format_probability(exact_probability))} · ajuste de mercado</em>"
            "</span>"
        )
    elif top_exact_scores:
        score, item = top_exact_scores[0]
        headline_tags.append(
            '<span class="market-tag gold featured">'
            "<strong>Marcador odds-aware pendiente</strong>"
            f"<b>{escape(score)}</b>"
            f"<em>top mercado exact score · momio {escape(_format_decimal_odd(item.get('median_decimal_odd')))}</em>"
            "</span>"
        )
    if match_winner:
        for key, label in (("home", "1"), ("draw", "X"), ("away", "2")):
            item = match_winner.get(key) or {}
            if item:
                headline_tags.append(_market_item(label, item, tone="blue"))

    goals_items: list[str] = []
    btts = markets.get("both_teams_score") or {}
    for key, label in (("yes", "Ambos anotan: si"), ("no", "Ambos anotan: no")):
        if key in btts:
            goals_items.append(_market_item(label, btts[key], tone="green"))
    ou_key = _closest_line_key(markets, "goals_over_under", 2.5)
    if ou_key:
        values = markets.get(ou_key) or {}
        line = ou_key.split(":", 1)[1]
        for key, label in (("over", f"Over {line}"), ("under", f"Under {line}")):
            if key in values:
                goals_items.append(_market_item(label, values[key], tone="green"))

    exact_items = [
        _market_item(score, item, tone="gold")
        for score, item in top_exact_scores
    ]
    prop_items: list[str] = []
    prop_markets = (
        ("cards_over_under", "Tarjetas"),
        ("corners_over_under", "Corners"),
        ("total_shotongoal", "Tiros a puerta"),
        ("home_total_shotongoal", "Tiros local"),
        ("away_total_shotongoal", "Tiros visita"),
        ("player_to_be_booked", "Jugador amonestado"),
        ("player_fouls_committed", "Faltas jugador"),
        ("player_shots_on_target", "Tiros a puerta jugador"),
        ("home_player_shots", "Tiros jugador local"),
        ("away_player_shots", "Tiros jugador visita"),
        ("goalkeeper_saves", "Atajadas"),
        ("player_saves", "Atajadas jugador"),
    )
    for prefix, title in prop_markets:
        candidate_keys = [
            key for key in markets if key == prefix or key.startswith(f"{prefix}:")
        ]
        for key in candidate_keys[:2]:
            values = markets.get(key) or {}
            line = key.split(":", 1)[1] if ":" in key else ""
            for selection, item in _top_market_items(markets, key, limit=2):
                label = f"{title} {line} {selection}".strip()
                prop_items.append(_market_item(label, item, tone="red"))
                if len(prop_items) >= 6:
                    break
            if len(prop_items) >= 6:
                break
        if len(prop_items) >= 6:
            break

    groups = [
        _market_group(
            "Resultado y formula",
            "Lectura combinada del modelo con el consenso prepartido.",
            headline_tags,
        ),
        _market_group(
            "Marcadores probables de mercado",
            "Top opciones del mercado de resultado exacto.",
            exact_items,
        ),
        _market_group(
            "Goles",
            "Senales principales de over/under y ambos equipos anotan.",
            goals_items,
        ),
        _market_group(
            "Props y disciplina",
            "Mercados adicionales disponibles: tarjetas, faltas, tiros, corners o atajadas.",
            prop_items,
        ),
    ]
    return (
        '<section class="market-dashboard">'
        '<div class="market-header">'
        "<div>"
        "<span>Mercados y apuestas</span>"
        "<h4>Modelo vs Mercado</h4>"
        "<p>Formula: Poisson + fortaleza del modelo + consenso 1-X-2 + O/U + BTTS + Exact Score.</p>"
        "</div>"
        f"{_model_market_signal(match, match_winner)}"
        "</div>"
        + "".join(group for group in groups if group)
        + '<p class="market-disclaimer">Informacion analitica para comparar senales; no es recomendacion financiera ni garantia de apuesta.</p>'
        "</section>"
    )


def _match_card_html(
    match: dict[str, Any],
    lineups_by_fixture: dict[tuple[str, str], dict[str, Any]],
    impacts_by_match_team: dict[tuple[str, str], list[dict[str, Any]]],
) -> str:
    fixture_id = match.get("api_fixture_id")
    fixture_key = str(int(fixture_id)) if fixture_id not in (None, "") and str(fixture_id) != "nan" else None
    home_norm = normalize_team_name(str(match["home_team"])) or str(match["home_team_norm"])
    away_norm = normalize_team_name(str(match["away_team"])) or str(match["away_team_norm"])
    home_lineup = lineups_by_fixture.get((fixture_key, home_norm)) if fixture_key else None
    away_lineup = lineups_by_fixture.get((fixture_key, away_norm)) if fixture_key else None
    home_impacts = impacts_by_match_team.get(
        (str(match["match_id"]), home_norm),
        impacts_by_match_team.get(
            (str(match["match_id"]), str(match["home_team_norm"])),
            [],
        ),
    )
    away_impacts = impacts_by_match_team.get(
        (str(match["match_id"]), away_norm),
        impacts_by_match_team.get(
            (str(match["match_id"]), str(match["away_team_norm"])),
            [],
        ),
    )
    predicted_score = match.get("predicted_score") or "Pendiente"
    hybrid_score = match.get("hybrid_predicted_score") or "No disponible"
    actual_score = _score_comparison(match.get("actual_home_goals"), match.get("actual_away_goals"))
    probability = float(match.get("probability") or 0.0)
    probability_text = f"{probability * 100:.1f}%" if probability else "n/d"
    hybrid_probability = float(match.get("hybrid_probability") or 0.0)
    hybrid_probability_text = f"{hybrid_probability * 100:.1f}%" if hybrid_probability else "n/d"
    home_win_probability = float(match.get("home_win_probability") or 0.0)
    draw_probability = float(match.get("draw_probability") or 0.0)
    away_win_probability = float(match.get("away_win_probability") or 0.0)
    outcome_probabilities_available = (
        home_win_probability + draw_probability + away_win_probability
    ) > 0.0
    freshness = match.get("data_freshness_at") or match.get("generated_at") or "n/d"
    fixture_badge = "Fixture API confirmado" if fixture_key else "Fixture API pendiente"
    lineup_available = home_lineup is not None and away_lineup is not None
    lineup_estimated = any(
        lineup and lineup.get("_lineup_source") == "web_estimated"
        for lineup in (home_lineup, away_lineup)
    )
    lineup_confirmed = lineup_available and not lineup_estimated
    if lineup_confirmed:
        lineup_badge = "Alineaciones confirmadas"
        summary_text = "Ver alineaciones confirmadas"
    elif lineup_estimated:
        lineup_badge = "Alineación web estimada"
        summary_text = "Ver alineaciones y fuentes"
    else:
        lineup_badge = "Alineaciones pendientes"
        summary_text = "Ver estado de alineaciones"
    evidence_release = match.get("evidence_release_id")
    evidence_label = "v1 fallback"
    if evidence_release:
        evidence_label = str(evidence_release)
        if int(match.get("evidence_release_preliminary") or 0):
            evidence_label += " (preliminar)"
    if outcome_probabilities_available:
        outcome_html = (
            '<div class="outcome-probabilities">'
            f'<span><b>1</b>{home_win_probability * 100:.1f}%</span>'
            f'<span><b>X</b>{draw_probability * 100:.1f}%</span>'
            f'<span><b>2</b>{away_win_probability * 100:.1f}%</span>'
            "</div>"
        )
    else:
        outcome_html = '<p class="outcome-empty">Logit pendiente: se conserva el pronóstico Poisson.</p>'

    return (
        '<article class="match-card">'
        '<div class="match-top">'
        f'<div class="match-date">{escape(str(match["date_cdmx"]))} · {escape(str(match["time_cdmx"]))} CDMX</div>'
        f'<div class="match-badges"><span>{escape(fixture_badge)}</span><span>{escape(lineup_badge)}</span></div>'
        "</div>"
        f'<h2>{escape(str(match["home_team"]))} <span>vs</span> {escape(str(match["away_team"]))}</h2>'
        '<div class="match-meta">'
        f'<span>Grupo {escape(str(match["group_name"]))}</span>'
        f'<span>{escape(str(match["stadium"]))}</span>'
        f'<span>{escape(str(match["stage"]))}</span>'
        "</div>"
        '<div class="score-grid">'
        '<div class="score-box predicted">'
        "<label>Poisson base</label>"
        f"<strong>{escape(str(predicted_score))}</strong>"
        f"<small>Probabilidad: {escape(probability_text)}</small>"
        "</div>"
        '<div class="score-box hybrid">'
        "<label>Híbrido Logit + Poisson</label>"
        f"<strong>{escape(str(hybrid_score))}</strong>"
        f"<small>Probabilidad: {escape(hybrid_probability_text)}</small>"
        "</div>"
        '<div class="score-box actual">'
        "<label>Resultado real</label>"
        f"<strong>{escape(actual_score)}</strong>"
        '<small>Se actualiza al cargar `actual_results`.</small>'
        "</div>"
        "</div>"
        f"{outcome_html}"
        f"{_render_market_section(match)}"
        f"{_render_evaluation(match)}"
        '<div class="impact-grid">'
        '<section class="impact-team">'
        f"<h4>{escape(str(match['home_team']))}</h4>"
        f"{_render_impact_summary(home_impacts)}"
        "</section>"
        '<section class="impact-team">'
        f"<h4>{escape(str(match['away_team']))}</h4>"
        f"{_render_impact_summary(away_impacts)}"
        "</section>"
        "</div>"
        '<details class="lineup-accordion">'
        f"<summary>{escape(summary_text)}</summary>"
        '<div class="lineup-grid">'
        f'{_render_team_lineup(str(match["home_team"]), home_lineup)}'
        f'{_render_team_lineup(str(match["away_team"]), away_lineup)}'
        "</div>"
        "</details>"
        f"{_render_prediction_timeline(match)}"
        '<div class="match-footer">'
        f"<span>Modelo: {escape(str(match.get('model_version') or 'n/d'))}</span>"
        f"<span>Logit: {escape(str(match.get('outcome_model_version') or 'n/d'))}</span>"
        f"<span>Evidencia: {escape(evidence_label)}</span>"
        f"<span>Datos: {escape(str(freshness))}</span>"
        f"<span>Generado: {escape(str(match.get('generated_at') or 'n/d'))}</span>"
        "</div>"
        "</article>"
    )


def render_predictions_html(
    match_rows: list[dict[str, Any]],
    lineups_by_fixture: dict[tuple[str, str], dict[str, Any]],
    impacts_by_match_team: dict[tuple[str, str], list[dict[str, Any]]],
    generated_at: datetime | None = None,
) -> str:
    date_label = "Sin fechas"
    updated_at_label = _format_cdmx_timestamp(generated_at)
    if match_rows:
        unique_dates = sorted({str(row["date_cdmx"]) for row in match_rows})
        date_label = unique_dates[0] if len(unique_dates) == 1 else f"{unique_dates[0]} a {unique_dates[-1]}"
    grouped_cards = []
    for date_value in sorted({str(row["date_cdmx"]) for row in match_rows}):
        date_rows = [
            row for row in match_rows if str(row["date_cdmx"]) == date_value
        ]
        cards = "".join(
            _match_card_html(match, lineups_by_fixture, impacts_by_match_team)
            for match in date_rows
        )
        grouped_cards.append(
            '<section class="matchday-group">'
            f"<h2>{escape(date_value)}</h2>"
            f"{cards}"
            "</section>"
        )
    cards = "".join(grouped_cards)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Predicciones Mundial 2026</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --surface: rgba(255, 255, 255, 0.90);
      --surface-strong: #ffffff;
      --ink: #111827;
      --muted: #5d6575;
      --wc-blue: #1746d2;
      --wc-red: #cf2434;
      --wc-green: #00843d;
      --wc-gold: #c99a18;
      --wc-cream: #fff8e7;
      --accent: var(--wc-blue);
      --accent-soft: rgba(23, 70, 210, 0.12);
      --success: var(--wc-green);
      --border: rgba(17, 24, 39, 0.12);
      --shadow: 0 24px 60px rgba(15, 23, 42, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 8% 0%, rgba(207, 36, 52, 0.20), transparent 28%),
        radial-gradient(circle at 88% 2%, rgba(0, 132, 61, 0.18), transparent 30%),
        radial-gradient(circle at 50% 12%, rgba(23, 70, 210, 0.20), transparent 34%),
        linear-gradient(180deg, #f9fbff 0%, #eef3fb 48%, #fff8e7 100%);
    }}
    .page {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .hero {{
      padding: 28px;
      border-radius: 28px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.96), rgba(255,248,231,0.90)),
        linear-gradient(90deg, var(--wc-blue), var(--wc-red), var(--wc-green));
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -80px -120px auto;
      width: 320px;
      height: 320px;
      border-radius: 999px;
      background: conic-gradient(from 30deg, var(--wc-blue), var(--wc-red), var(--wc-green), var(--wc-gold), var(--wc-blue));
      opacity: 0.11;
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 5vw, 3.4rem);
      line-height: 1;
      letter-spacing: -0.04em;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      max-width: 760px;
      line-height: 1.6;
    }}
    .hero-stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 18px;
    }}
    .hero-stats span {{
      padding: 10px 14px;
      border-radius: 999px;
      background: var(--surface-strong);
      border: 1px solid var(--border);
      font-size: 0.95rem;
    }}
    .matches {{
      display: grid;
      gap: 18px;
      margin-top: 24px;
    }}
    .matchday-group {{
      display: grid;
      gap: 18px;
    }}
    .matchday-group > h2 {{
      margin: 10px 4px 0;
      font-size: 1.25rem;
      color: var(--muted);
      letter-spacing: 0.04em;
    }}
    .match-card {{
      padding: 22px;
      border-radius: 24px;
      background: var(--surface);
      backdrop-filter: blur(8px);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }}
    .match-top, .match-meta, .match-footer, .match-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px 12px;
      align-items: center;
      justify-content: space-between;
    }}
    .match-date {{
      font-size: 0.95rem;
      color: var(--muted);
      font-weight: 600;
    }}
    .match-badges span, .match-meta span, .match-footer span {{
      border-radius: 999px;
      border: 1px solid var(--border);
      padding: 7px 12px;
      background: rgba(255,255,255,0.65);
      font-size: 0.88rem;
      color: var(--muted);
    }}
    .match-card h2 {{
      margin: 18px 0 10px;
      font-size: clamp(1.5rem, 4vw, 2.2rem);
      line-height: 1.1;
      letter-spacing: -0.03em;
    }}
    .match-card h2 span {{
      color: var(--accent);
      font-weight: 500;
    }}
    .score-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin: 18px 0 14px;
    }}
    .score-box {{
      padding: 18px;
      border-radius: 20px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.75);
    }}
    .score-box.predicted {{
      background: linear-gradient(135deg, rgba(23,70,210,0.14), rgba(255,255,255,0.86));
    }}
    .score-box.actual {{
      background: linear-gradient(135deg, rgba(0,132,61,0.13), rgba(255,255,255,0.86));
    }}
    .score-box.hybrid {{
      background: linear-gradient(135deg, rgba(201,154,24,0.18), rgba(255,255,255,0.86));
    }}
    .score-box label {{
      display: block;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .score-box strong {{
      display: block;
      font-size: 2rem;
      letter-spacing: -0.04em;
    }}
    .score-box small {{
      display: block;
      margin-top: 6px;
      color: var(--muted);
    }}
    .outcome-probabilities {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 0 0 14px;
    }}
    .outcome-probabilities span {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 11px 14px;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: rgba(255,255,255,0.66);
      color: var(--muted);
    }}
    .outcome-probabilities b {{
      color: var(--ink);
      font-size: 1.05rem;
    }}
    .outcome-empty {{
      margin: 0 0 14px;
      color: var(--muted);
    }}
    .market-dashboard {{
      display: grid;
      gap: 12px;
      padding: 16px;
      margin: 0 0 14px;
      border: 1px solid var(--border);
      border-radius: 22px;
      background:
        linear-gradient(135deg, rgba(23,70,210,0.09), rgba(0,132,61,0.07)),
        rgba(255,255,255,0.76);
    }}
    .market-header {{
      display: flex;
      gap: 14px;
      align-items: flex-start;
      justify-content: space-between;
    }}
    .market-header > div > span {{
      display: inline-flex;
      margin-bottom: 5px;
      color: var(--wc-blue);
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .market-header h4 {{
      margin: 0;
      font-size: 1.25rem;
      letter-spacing: -0.03em;
    }}
    .market-header p, .market-panel p {{
      margin: 4px 0 0;
      color: var(--muted);
      line-height: 1.45;
    }}
    .signal-chip {{
      flex: 0 0 auto;
      max-width: 270px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.82);
      color: var(--muted);
      font-size: 0.86rem;
      font-weight: 700;
      text-align: center;
    }}
    .signal-chip.alta {{
      color: #8a1722;
      background: rgba(207,36,52,0.11);
      border-color: rgba(207,36,52,0.24);
    }}
    .signal-chip.media {{
      color: #8b650f;
      background: rgba(201,154,24,0.15);
      border-color: rgba(201,154,24,0.28);
    }}
    .signal-chip.baja, .signal-chip.neutral {{
      color: #145c35;
      background: rgba(0,132,61,0.10);
      border-color: rgba(0,132,61,0.22);
    }}
    .market-panel {{
      padding: 14px;
      border-radius: 18px;
      background: rgba(255,255,255,0.70);
      border: 1px solid rgba(17,24,39,0.08);
    }}
    .market-panel h5 {{
      margin: 0;
      font-size: 0.95rem;
    }}
    .market-tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 11px;
    }}
    .market-tag {{
      display: grid;
      min-width: 142px;
      gap: 3px;
      padding: 11px 12px;
      border-radius: 16px;
      border: 1px solid rgba(17,24,39,0.10);
      background: rgba(255,255,255,0.84);
      box-shadow: 0 8px 20px rgba(15,23,42,0.06);
    }}
    .market-tag strong {{
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .market-tag b {{
      font-size: 1.12rem;
      letter-spacing: -0.03em;
    }}
    .market-tag em {{
      font-style: normal;
      color: var(--muted);
      font-size: 0.78rem;
    }}
    .market-tag.featured {{
      min-width: 190px;
    }}
    .market-tag.blue {{
      border-color: rgba(23,70,210,0.24);
      background: linear-gradient(135deg, rgba(23,70,210,0.12), rgba(255,255,255,0.92));
    }}
    .market-tag.green {{
      border-color: rgba(0,132,61,0.24);
      background: linear-gradient(135deg, rgba(0,132,61,0.12), rgba(255,255,255,0.92));
    }}
    .market-tag.red {{
      border-color: rgba(207,36,52,0.24);
      background: linear-gradient(135deg, rgba(207,36,52,0.10), rgba(255,255,255,0.92));
    }}
    .market-tag.gold {{
      border-color: rgba(201,154,24,0.30);
      background: linear-gradient(135deg, rgba(201,154,24,0.18), rgba(255,255,255,0.92));
    }}
    .market-disclaimer {{
      margin: 0;
      color: var(--muted);
      font-size: 0.84rem;
      line-height: 1.45;
    }}
    .market-empty {{
      margin: 0 0 14px;
      color: var(--muted);
    }}
    .evaluation-box {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .evaluation-box span {{
      padding: 9px 11px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.66);
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .evaluation-empty, .timeline-empty {{
      color: var(--muted);
    }}
    .lineup-accordion {{
      margin-top: 14px;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: rgba(255,255,255,0.55);
      overflow: hidden;
    }}
    .impact-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 14px;
    }}
    .impact-team {{
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.6);
    }}
    .impact-team h4, .impact-box h5 {{
      margin: 0 0 10px;
    }}
    .impact-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 8px;
    }}
    .impact-list li {{
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(255,250,242,0.82);
    }}
    .impact-list li span {{
      color: var(--muted);
      text-transform: capitalize;
    }}
    .impact-list li em {{
      font-style: normal;
      font-weight: 700;
      color: var(--accent);
    }}
    .impact-axis {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}
    .impact-axis span {{
      padding: 8px 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.72);
      border: 1px solid rgba(30,36,48,0.08);
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .impact-empty {{
      margin: 0;
      color: var(--muted);
    }}
    .lineup-accordion summary {{
      cursor: pointer;
      list-style: none;
      padding: 16px 18px;
      font-weight: 700;
    }}
    .timeline-accordion {{
      margin-top: 12px;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: rgba(255,255,255,0.5);
    }}
    .timeline-accordion summary {{
      cursor: pointer;
      padding: 14px 16px;
      font-weight: 700;
    }}
    .timeline-list {{
      list-style: none;
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0 16px 16px;
    }}
    .timeline-list li {{
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto auto;
      gap: 12px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(255,250,242,0.82);
    }}
    .timeline-list time, .timeline-list span {{
      color: var(--muted);
      font-size: 0.86rem;
    }}
    .lineup-accordion summary::-webkit-details-marker {{
      display: none;
    }}
    .lineup-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      padding: 0 18px 18px;
    }}
    .lineup-team {{
      padding: 16px;
      border-radius: 18px;
      background: rgba(255,250,242,0.9);
      border: 1px solid rgba(30,36,48,0.08);
    }}
    .lineup-team h4, .lineup-team h5 {{
      margin: 0 0 10px;
    }}
    .lineup-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .lineup-estimate-note {{
      display: grid;
      gap: 6px;
      margin-bottom: 12px;
      padding: 11px 12px;
      border-radius: 12px;
      background: rgba(207,36,52,0.10);
      border: 1px solid rgba(207,36,52,0.20);
      color: var(--muted);
      font-size: 0.86rem;
    }}
    .lineup-estimate-note strong {{
      color: var(--accent);
    }}
    .lineup-estimate-note a {{
      color: var(--success);
      font-weight: 700;
    }}
    .player-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 8px;
    }}
    .player-list li {{
      display: grid;
      grid-template-columns: 52px 1fr 56px;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(255,255,255,0.72);
      border: 1px solid rgba(30,36,48,0.06);
    }}
    .player-number {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 32px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }}
    .player-name {{
      font-weight: 600;
    }}
    .player-position {{
      justify-self: end;
      color: var(--success);
      font-weight: 700;
    }}
    .lineup-empty {{
      margin: 0;
      color: var(--muted);
    }}
    .match-footer {{
      margin-top: 14px;
      justify-content: flex-start;
    }}
    @media (max-width: 820px) {{
      .score-grid, .lineup-grid, .impact-grid, .outcome-probabilities {{
        grid-template-columns: 1fr;
      }}
      .timeline-list li {{
        grid-template-columns: 1fr;
      }}
      .market-header {{
        display: grid;
      }}
      .signal-chip {{
        max-width: none;
        width: 100%;
      }}
      .market-tag {{
        width: 100%;
      }}
      .page {{
        padding: 20px 14px 42px;
      }}
      .hero, .match-card {{
        padding: 18px;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <h1>Predicciones Mundial 2026</h1>
      <p>
        Reporte estático para comparar el marcador pronosticado contra el resultado real de cada partido.
        Incluye horario en Ciudad de México, estadio y el estado más reciente de las alineaciones oficiales o estimadas.
      </p>
      <div class="hero-stats">
        <span>Fechas: {escape(date_label)}</span>
        <span>Partidos: {len(match_rows)}</span>
        <span>Fuente operativa: SQLite local</span>
        <span>Ultima actualizacion: {escape(updated_at_label)}</span>
      </div>
    </section>
    <section class="matches">
      {cards}
    </section>
  </main>
</body>
</html>
"""


def build_predictions_html_report(dates: list[str], output_path: Path | None = None) -> Path:
    settings = get_settings()
    connection = get_connection(settings.db_path)
    match_rows = _load_match_rows(connection, dates)
    _attach_odds_consensus(connection, match_rows)
    fixture_ids = []
    for row in match_rows:
        fixture_id = row.get("api_fixture_id")
        if fixture_id not in (None, "") and str(fixture_id) != "nan":
            fixture_ids.append(str(int(fixture_id)))
    lineups_by_fixture = _load_lineups_by_fixture(connection, sorted(set(fixture_ids)))
    impacts_by_match_team = _load_prediction_impacts(connection, [str(row["match_id"]) for row in match_rows])
    _fill_snapshot_impacts(connection, match_rows, impacts_by_match_team)
    html = render_predictions_html(
        match_rows,
        lineups_by_fixture,
        impacts_by_match_team,
    )
    if output_path is None:
        date_label = dates[0] if len(dates) == 1 else f"{dates[0]}_a_{dates[-1]}"
        output_path = settings.predictions_dir / f"predicciones_{date_label}.html"
    output_path.write_text(html, encoding="utf-8")
    today = datetime.now(ZoneInfo(settings.local_timezone)).date().isoformat()
    if (
        output_path.parent.resolve() == settings.predictions_dir.resolve()
        and today in dates
    ):
        (settings.predictions_dir / "index.html").write_text(html, encoding="utf-8")
    return output_path
