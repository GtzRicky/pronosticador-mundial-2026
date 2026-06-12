from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import sqlite3
from typing import Any

from quiniela.config import get_settings
from quiniela.db import fetch_dataframe, get_connection


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
    return (
        '<section class="lineup-team">'
        f"<h4>{escape(team_name)}</h4>"
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
            lineups[key] = json.loads(row["source_json"])
    return lineups


def _load_prediction_impacts(connection: sqlite3.Connection, match_ids: list[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if not match_ids:
        return {}
    placeholders = ",".join("?" for _ in match_ids)
    rows = connection.execute(
        f"""
        SELECT match_id, team_norm, source_json
        FROM prediction_player_impacts
        WHERE match_id IN ({placeholders})
        ORDER BY match_id, net_impact DESC, id ASC
        """,
        match_ids,
    ).fetchall()
    impacts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["match_id"]), str(row["team_norm"]))
        impacts.setdefault(key, []).append(json.loads(row["source_json"]))
    return impacts


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
            lp.generated_at,
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


def _score_comparison(actual_home_goals: Any, actual_away_goals: Any) -> str:
    if actual_home_goals is None or actual_away_goals is None:
        return "Pendiente"
    if str(actual_home_goals) == "nan" or str(actual_away_goals) == "nan":
        return "Pendiente"
    return f"{int(actual_home_goals)}-{int(actual_away_goals)}"


def _match_card_html(
    match: dict[str, Any],
    lineups_by_fixture: dict[tuple[str, str], dict[str, Any]],
    impacts_by_match_team: dict[tuple[str, str], list[dict[str, Any]]],
) -> str:
    fixture_id = match.get("api_fixture_id")
    fixture_key = str(int(fixture_id)) if fixture_id not in (None, "") and str(fixture_id) != "nan" else None
    home_lineup = lineups_by_fixture.get((fixture_key, match["home_team_norm"])) if fixture_key else None
    away_lineup = lineups_by_fixture.get((fixture_key, match["away_team_norm"])) if fixture_key else None
    home_impacts = impacts_by_match_team.get((str(match["match_id"]), str(match["home_team_norm"])), [])
    away_impacts = impacts_by_match_team.get((str(match["match_id"]), str(match["away_team_norm"])), [])
    predicted_score = match.get("predicted_score") or "Pendiente"
    actual_score = _score_comparison(match.get("actual_home_goals"), match.get("actual_away_goals"))
    probability = float(match.get("probability") or 0.0)
    probability_text = f"{probability * 100:.1f}%" if probability else "n/d"
    fixture_badge = "Fixture API confirmado" if fixture_key else "Fixture API pendiente"
    lineup_confirmed = home_lineup is not None and away_lineup is not None
    lineup_badge = "Alineaciones confirmadas" if lineup_confirmed else "Alineaciones pendientes"
    summary_text = "Ver alineaciones confirmadas" if lineup_confirmed else "Ver estado de alineaciones"

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
        "<label>Pronóstico</label>"
        f"<strong>{escape(str(predicted_score))}</strong>"
        f"<small>Probabilidad: {escape(probability_text)}</small>"
        "</div>"
        '<div class="score-box actual">'
        "<label>Resultado real</label>"
        f"<strong>{escape(actual_score)}</strong>"
        '<small>Se actualiza al cargar `actual_results`.</small>'
        "</div>"
        "</div>"
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
        '<div class="match-footer">'
        f"<span>Modelo: {escape(str(match.get('model_version') or 'n/d'))}</span>"
        f"<span>Generado: {escape(str(match.get('generated_at') or 'n/d'))}</span>"
        "</div>"
        "</article>"
    )


def render_predictions_html(
    match_rows: list[dict[str, Any]],
    lineups_by_fixture: dict[tuple[str, str], dict[str, Any]],
    impacts_by_match_team: dict[tuple[str, str], list[dict[str, Any]]],
) -> str:
    date_label = "Sin fechas"
    if match_rows:
        unique_dates = sorted({str(row["date_cdmx"]) for row in match_rows})
        date_label = unique_dates[0] if len(unique_dates) == 1 else f"{unique_dates[0]} a {unique_dates[-1]}"
    cards = "".join(_match_card_html(match, lineups_by_fixture, impacts_by_match_team) for match in match_rows)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Predicciones Mundial 2026</title>
  <style>
    :root {{
      --bg: #f5efe3;
      --surface: rgba(255, 252, 247, 0.88);
      --surface-strong: #fffaf2;
      --ink: #1e2430;
      --muted: #5f6777;
      --accent: #b6462a;
      --accent-soft: #e9c9b2;
      --success: #1f6a52;
      --border: rgba(30, 36, 48, 0.12);
      --shadow: 0 20px 50px rgba(34, 28, 19, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(182, 70, 42, 0.22), transparent 34%),
        radial-gradient(circle at top right, rgba(31, 106, 82, 0.18), transparent 30%),
        linear-gradient(180deg, #f9f2e6 0%, #efe4d3 100%);
    }}
    .page {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .hero {{
      padding: 28px;
      border-radius: 28px;
      background: linear-gradient(135deg, rgba(255,250,242,0.96), rgba(245,236,222,0.92));
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
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
      grid-template-columns: repeat(2, minmax(0, 1fr));
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
      background: linear-gradient(135deg, rgba(182,70,42,0.12), rgba(255,255,255,0.82));
    }}
    .score-box.actual {{
      background: linear-gradient(135deg, rgba(31,106,82,0.12), rgba(255,255,255,0.82));
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
      .score-grid, .lineup-grid, .impact-grid {{
        grid-template-columns: 1fr;
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
        Incluye horario en Ciudad de México, estadio y el estado más reciente de las alineaciones confirmadas.
      </p>
      <div class="hero-stats">
        <span>Fechas: {escape(date_label)}</span>
        <span>Partidos: {len(match_rows)}</span>
        <span>Fuente operativa: SQLite local</span>
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
    fixture_ids = []
    for row in match_rows:
        fixture_id = row.get("api_fixture_id")
        if fixture_id not in (None, "") and str(fixture_id) != "nan":
            fixture_ids.append(str(int(fixture_id)))
    lineups_by_fixture = _load_lineups_by_fixture(connection, sorted(set(fixture_ids)))
    impacts_by_match_team = _load_prediction_impacts(connection, [str(row["match_id"]) for row in match_rows])
    html = render_predictions_html(match_rows, lineups_by_fixture, impacts_by_match_team)
    if output_path is None:
        date_label = dates[0] if len(dates) == 1 else f"{dates[0]}_a_{dates[-1]}"
        output_path = settings.predictions_dir / f"predicciones_{date_label}.html"
    output_path.write_text(html, encoding="utf-8")
    return output_path
