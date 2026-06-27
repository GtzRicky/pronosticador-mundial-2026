from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import re
import sqlite3
from typing import Any
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import requests

from quiniela.config import get_settings
from quiniela.infrastructure.competition_config import (
    CompetitionContext,
    resolve_competition_context,
)
from quiniela.db import fetch_dataframe, get_connection, insert_lineup_estimate
from quiniela.name_maps import (
    normalize_team_name,
    normalize_text,
    preferred_team_search_name,
)


SEARCH_ENDPOINTS = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    "https://www.bing.com/news/search?q={query}&format=rss",
)
LINEUP_CONTEXT = (
    "predicted lineup",
    "probable lineup",
    "possible lineup",
    "starting xi",
    "expected xi",
    "team news",
    "alineacion probable",
    "posible alineacion",
    "once inicial",
)


@dataclass(frozen=True)
class NewsArticle:
    title: str
    url: str
    published_at: str | None
    text: str


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return " ".join(self.parts)


class NewsSearchClient:
    def __init__(
        self,
        timeout_seconds: float = 8,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                )
            }
        )

    def search(self, query: str, max_articles: int = 6) -> list[NewsArticle]:
        entries_by_provider: list[list[dict[str, str | None]]] = []
        seen_urls: set[str] = set()
        last_error: Exception | None = None
        per_provider = max(2, (max_articles + len(SEARCH_ENDPOINTS) - 1) // len(SEARCH_ENDPOINTS))
        for endpoint in SEARCH_ENDPOINTS:
            provider_entries: list[dict[str, str | None]] = []
            try:
                response = self.session.get(
                    endpoint.format(query=quote_plus(query)),
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for item in root.findall(".//item"):
                    url = (item.findtext("link") or "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    provider_entries.append(
                        {
                            "title": (item.findtext("title") or "").strip(),
                            "url": url,
                            "published_at": _parse_published_at(
                                item.findtext("pubDate")
                            ),
                            "description": (
                                item.findtext("description") or ""
                            ).strip(),
                        }
                    )
                    if len(provider_entries) >= per_provider:
                        break
            except (requests.RequestException, ET.ParseError) as exc:
                last_error = exc
            entries_by_provider.append(provider_entries)

        entries: list[dict[str, str | None]] = []
        for index in range(per_provider):
            for provider_entries in entries_by_provider:
                if index < len(provider_entries):
                    entries.append(provider_entries[index])
                if len(entries) >= max_articles:
                    break
            if len(entries) >= max_articles:
                break
        if not entries and last_error is not None:
            raise last_error

        articles: list[NewsArticle] = []
        for entry in entries[:max_articles]:
            article_text = str(entry["description"] or "")
            try:
                response = self.session.get(
                    str(entry["url"]),
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                parser = _VisibleTextParser()
                parser.feed(response.text[:500_000])
                visible_text = parser.text()
                if visible_text:
                    article_text = visible_text
            except requests.RequestException:
                pass
            articles.append(
                NewsArticle(
                    title=str(entry["title"] or ""),
                    url=str(entry["url"]),
                    published_at=entry["published_at"],
                    text=article_text,
                )
            )
        return articles


def _parse_published_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _normalize_search_text(value: str) -> str:
    return normalize_text(value).replace("_", " ")


def _official_lineup_complete(
    connection: sqlite3.Connection,
    fixture_id: str,
    team_norm: str,
) -> bool:
    row = connection.execute(
        """
        SELECT source_json
        FROM historical_lineups
        WHERE fixture_id = ? AND team_norm = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (fixture_id, team_norm),
    ).fetchone()
    if row is None:
        return False
    try:
        payload = json.loads(row["source_json"])
    except json.JSONDecodeError:
        return False
    return len(payload.get("startXI") or []) >= 11


def _roster(
    connection: sqlite3.Connection,
    team_norm: str,
) -> list[dict[str, Any]]:
    frame = fetch_dataframe(
        connection,
        """
        SELECT
            p.api_player_id,
            COALESCE(p.api_player_name, p.player) AS player_name,
            p.player_norm,
            COALESCE(p.api_position, p.position_group) AS position,
            p.position_group,
            COALESCE(MAX(ps.lineups), 0) AS lineups,
            COALESCE(MAX(ps.minutes), 0) AS minutes
        FROM players p
        LEFT JOIN player_season_stats ps
          ON ps.team_norm = p.team_norm
         AND (
             (p.api_player_id IS NOT NULL AND ps.api_player_id = p.api_player_id)
             OR (p.api_player_id IS NULL AND ps.player_norm = p.player_norm)
         )
        WHERE p.team_norm = ? AND p.is_active = 1
        GROUP BY
            p.id, p.api_player_id, p.api_player_name, p.player, p.player_norm,
            p.api_position, p.position_group
        ORDER BY lineups DESC, minutes DESC, p.id
        """,
        (team_norm,),
    )
    return frame.to_dict(orient="records")


def _recent_starters(
    connection: sqlite3.Connection,
    team_norm: str,
    kickoff_at: str,
) -> list[str]:
    row = connection.execute(
        """
        SELECT hl.source_json
        FROM historical_lineups hl
        LEFT JOIN historical_matches hm ON hm.fixture_id = hl.fixture_id
        WHERE hl.team_norm = ?
          AND (hm.match_date IS NULL OR hm.match_date < ?)
        ORDER BY hm.match_date DESC, hl.id DESC
        LIMIT 1
        """,
        (team_norm, kickoff_at),
    ).fetchone()
    if row is None:
        return []
    try:
        payload = json.loads(row["source_json"])
    except json.JSONDecodeError:
        return []
    return [
        normalize_text(str(item.get("player", {}).get("name") or ""))
        for item in payload.get("startXI") or []
    ]


def _player_mentions(
    roster: list[dict[str, Any]],
    articles: list[NewsArticle],
) -> tuple[dict[str, float], dict[str, set[str]]]:
    surname_counts = Counter()
    for player in roster:
        tokens = str(player["player_norm"]).split("_")
        if tokens:
            surname_counts[tokens[-1]] += 1

    scores: dict[str, float] = {}
    source_hits: dict[str, set[str]] = {}
    for player in roster:
        player_norm = str(player["player_norm"])
        full_name = player_norm.replace("_", " ")
        tokens = player_norm.split("_")
        surname = tokens[-1] if tokens else ""
        patterns = [full_name]
        if len(surname) >= 4 and surname_counts[surname] == 1:
            patterns.append(surname)

        for article in articles:
            title = _normalize_search_text(article.title)
            body = _normalize_search_text(article.text)
            article_score = 0.0
            for pattern in patterns:
                if not pattern:
                    continue
                if re.search(rf"\b{re.escape(pattern)}\b", title):
                    article_score = max(article_score, 4.0)
                for match in re.finditer(rf"\b{re.escape(pattern)}\b", body):
                    context = body[
                        max(0, match.start() - 220) : match.end() + 220
                    ]
                    context_bonus = 3.0 if any(
                        phrase in context for phrase in LINEUP_CONTEXT
                    ) else 1.0
                    article_score = max(article_score, context_bonus)
            if article_score:
                scores[player_norm] = scores.get(player_norm, 0.0) + article_score
                source_hits.setdefault(player_norm, set()).add(article.url)
    return scores, source_hits


def _position_code(player: dict[str, Any]) -> str:
    text = str(player.get("position_group") or player.get("position") or "").lower()
    if text.startswith("goal") or text == "g":
        return "G"
    if text.startswith("def") or text == "d":
        return "D"
    if text.startswith("mid") or text == "m":
        return "M"
    if text.startswith(("for", "att")) or text == "f":
        return "F"
    return ""


def _select_starters(
    roster: list[dict[str, Any]],
    mention_scores: dict[str, float],
    source_hits: dict[str, set[str]],
    recent_starters: list[str],
) -> tuple[list[dict[str, Any]], int]:
    recent_rank = {
        player_norm: len(recent_starters) - index
        for index, player_norm in enumerate(recent_starters)
    }
    ranked = sorted(
        roster,
        key=lambda player: (
            mention_scores.get(str(player["player_norm"]), 0.0),
            len(source_hits.get(str(player["player_norm"]), set())),
            recent_rank.get(str(player["player_norm"]), 0),
            float(player.get("lineups") or 0),
            float(player.get("minutes") or 0),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    goalkeepers = [player for player in ranked if _position_code(player) == "G"]
    if goalkeepers:
        selected.append(goalkeepers[0])
    selected.extend(player for player in ranked if player not in selected)
    selected = selected[:11]
    direct_count = sum(
        1
        for player in selected
        if mention_scores.get(str(player["player_norm"]), 0.0) > 0
    )
    return selected, direct_count


def _confidence(
    direct_count: int,
    selected: list[dict[str, Any]],
    source_hits: dict[str, set[str]],
    source_count: int,
) -> float:
    agreement = sum(
        1
        for player in selected
        if len(source_hits.get(str(player["player_norm"]), set())) >= 2
    )
    value = (
        0.25
        + 0.38 * min(direct_count / 11.0, 1.0)
        + 0.10 * min(source_count / 3.0, 1.0)
        + 0.12 * min(agreement / 6.0, 1.0)
    )
    return round(min(value, 0.85), 3)


def _build_payload(
    team_name: str,
    selected: list[dict[str, Any]],
    articles: list[NewsArticle],
    confidence: float,
    direct_count: int,
    generated_at: str,
) -> dict[str, Any]:
    sources = [
        {
            "title": article.title,
            "url": article.url,
            "published_at": article.published_at,
        }
        for article in articles
    ]
    return {
        "team": {"name": team_name},
        "formation": "Estimada",
        "coach": {},
        "startXI": [
            {
                "player": {
                    "id": player.get("api_player_id"),
                    "name": player.get("player_name"),
                    "number": None,
                    "pos": _position_code(player),
                }
            }
            for player in selected
        ],
        "substitutes": [],
        "_lineup_source": "web_estimated",
        "_confidence": confidence,
        "_direct_matches": direct_count,
        "_sources": sources,
        "_generated_at": generated_at,
    }


def refresh_web_lineup_fallback(
    match_id: str,
    *,
    window_label: str = "manual",
    connection: sqlite3.Connection | None = None,
    now: datetime | None = None,
    force: bool = False,
    client: NewsSearchClient | None = None,
    competition_context: CompetitionContext | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    context = competition_context or resolve_competition_context()
    connection = connection or get_connection(settings.db_path)
    if not settings.web_lineup_fallback_enabled and not force:
        return {"status": "disabled", "match_id": match_id}

    match = connection.execute(
        """
        SELECT match_id, datetime_cdmx, home_team, away_team,
               home_team_norm, away_team_norm, api_fixture_id
        FROM matches
        WHERE match_id = ?
          AND competition_id = ?
          AND season_id = ?
        """,
        (match_id, context.competition_id, context.season_id),
    ).fetchone()
    if match is None or match["api_fixture_id"] is None:
        return {"status": "match_or_fixture_missing", "match_id": match_id}

    local_tz = ZoneInfo(settings.local_timezone)
    kickoff = datetime.fromisoformat(str(match["datetime_cdmx"]))
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=local_tz)
    local_now = (now or datetime.now(local_tz)).astimezone(local_tz)
    minutes_to_kickoff = (kickoff.astimezone(local_tz) - local_now).total_seconds() / 60
    if not force and (
        minutes_to_kickoff < 0
        or minutes_to_kickoff > settings.web_lineup_fallback_minutes
    ):
        return {
            "status": "outside_window",
            "match_id": match_id,
            "minutes_to_kickoff": round(minutes_to_kickoff, 1),
        }

    fixture_id = str(int(match["api_fixture_id"]))
    teams = (
        (
            normalize_team_name(str(match["home_team"])),
            str(match["home_team"]),
            str(match["away_team"]),
        ),
        (
            normalize_team_name(str(match["away_team"])),
            str(match["away_team"]),
            str(match["home_team"]),
        ),
    )
    missing = [
        team for team in teams if not _official_lineup_complete(connection, fixture_id, team[0])
    ]
    if not missing:
        return {"status": "official_lineups_available", "match_id": match_id}

    client = client or NewsSearchClient(settings.web_lineup_timeout_seconds)
    generated_at = local_now.isoformat()
    results: list[dict[str, Any]] = []
    for team_norm, team_name, opponent_name in missing:
        search_team = preferred_team_search_name(team_name)
        search_opponent = preferred_team_search_name(opponent_name)
        date_token = kickoff.date().isoformat()
        queries = [
            f'"{search_team}" "{search_opponent}" predicted lineup {date_token}',
            f'"{search_team}" starting XI team news {date_token}',
        ]
        article_map: dict[str, NewsArticle] = {}
        errors: list[str] = []
        for query in queries:
            try:
                for article in client.search(
                    query,
                    max_articles=settings.web_lineup_max_articles,
                ):
                    article_map.setdefault(article.url, article)
            except (requests.RequestException, ET.ParseError, ValueError) as exc:
                errors.append(str(exc))

        articles = list(article_map.values())[: settings.web_lineup_max_articles]
        roster = _roster(connection, team_norm)
        mention_scores, source_hits = _player_mentions(roster, articles)
        selected, direct_count = _select_starters(
            roster,
            mention_scores,
            source_hits,
            _recent_starters(connection, team_norm, str(match["datetime_cdmx"])),
        )
        if (
            len(selected) < 11
            or direct_count < settings.web_lineup_min_direct_players
            or not articles
        ):
            results.append(
                {
                    "team": team_name,
                    "status": "insufficient_web_evidence",
                    "players": len(selected),
                    "direct_matches": direct_count,
                    "sources": len(articles),
                    "errors": errors,
                }
            )
            continue

        confidence = _confidence(
            direct_count,
            selected,
            source_hits,
            len(articles),
        )
        payload = _build_payload(
            team_name,
            selected,
            articles,
            confidence,
            direct_count,
            generated_at,
        )
        sources = payload["_sources"]
        created = insert_lineup_estimate(
            connection,
            fixture_id=fixture_id,
            match_id=match_id,
            team_norm=team_norm,
            window_label=window_label,
            confidence=confidence,
            queries=queries,
            sources=sources,
            payload=payload,
            generated_at=generated_at,
        )
        results.append(
            {
                "team": team_name,
                "status": "created" if created else "already_exists",
                "confidence": confidence,
                "direct_matches": direct_count,
                "sources": len(sources),
            }
        )
    return {
        "status": "completed",
        "match_id": match_id,
        "window_label": window_label,
        "teams": results,
    }
