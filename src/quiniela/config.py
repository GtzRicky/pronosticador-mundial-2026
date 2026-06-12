from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
import os


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    raw_dir: Path
    processed_dir: Path
    db_dir: Path
    outputs_dir: Path
    bundles_dir: Path
    predictions_dir: Path
    logs_dir: Path
    public_dir: Path
    model_artifacts_dir: Path
    api_football_key: str
    api_football_host: str
    api_daily_limit: int
    db_path: Path
    local_timezone: str

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.raw_dir,
            self.processed_dir,
            self.db_dir,
            self.outputs_dir,
            self.bundles_dir,
            self.predictions_dir,
            self.logs_dir,
            self.public_dir,
            self.model_artifacts_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env")

    data_dir = ROOT_DIR / "data"
    raw_dir = data_dir / "raw"
    processed_dir = data_dir / "processed"
    db_dir = data_dir / "db"
    outputs_dir = ROOT_DIR / "outputs"
    bundles_dir = outputs_dir / "bundles"
    predictions_dir = outputs_dir / "predictions"
    logs_dir = outputs_dir / "logs"
    public_dir = data_dir / "public"
    model_artifacts_dir = processed_dir / "model_artifacts"

    db_path_value = os.getenv("DB_PATH", "data/db/quiniela.db")
    db_path = ROOT_DIR / db_path_value

    settings = Settings(
        root_dir=ROOT_DIR,
        data_dir=data_dir,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        db_dir=db_dir,
        outputs_dir=outputs_dir,
        bundles_dir=bundles_dir,
        predictions_dir=predictions_dir,
        logs_dir=logs_dir,
        public_dir=public_dir,
        model_artifacts_dir=model_artifacts_dir,
        api_football_key=os.getenv("API_FOOTBALL_KEY", "").strip(),
        api_football_host=os.getenv("API_FOOTBALL_HOST", "v3.football.api-sports.io").strip(),
        api_daily_limit=int(os.getenv("API_DAILY_LIMIT", "100")),
        db_path=db_path,
        local_timezone=os.getenv("LOCAL_TIMEZONE", "America/Mexico_City").strip(),
    )
    settings.ensure_directories()
    return settings
