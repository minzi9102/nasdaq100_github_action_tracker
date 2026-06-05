from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class ProjectPaths:
    root: Path = ROOT
    config_dir: Path = ROOT / "config"
    raw_dir: Path = ROOT / "data" / "raw"
    processed_dir: Path = ROOT / "data" / "processed"
    cache_dir: Path = ROOT / "data" / "cache"
    tiingo_price_cache_dir: Path = ROOT / "data" / "cache" / "prices" / "tiingo"
    reports_latest_dir: Path = ROOT / "reports" / "latest"
    reports_archive_dir: Path = ROOT / "reports" / "archive"
    state_dir: Path = ROOT / "state"


class Settings:
    def __init__(self) -> None:
        load_dotenv(ROOT / ".env")
        self.paths = ProjectPaths()
        self.sources = load_yaml(self.paths.config_dir / "sources.yml")
        self.symbols = load_yaml(self.paths.config_dir / "symbols.yml")
        self.fred_series = load_yaml(self.paths.config_dir / "fred_series.yml")
        self.pipeline = load_yaml(self.paths.config_dir / "pipeline.yml")
        self.api_limits = load_yaml(self.paths.config_dir / "api_limits.yml")

    def get_secret(self, env_name: str) -> str | None:
        value = os.getenv(env_name)
        if value:
            return value.strip()
        return None

    def ensure_dirs(self) -> None:
        for p in [
            self.paths.raw_dir,
            self.paths.processed_dir,
            self.paths.cache_dir,
            self.paths.tiingo_price_cache_dir,
            self.paths.reports_latest_dir,
            self.paths.reports_archive_dir,
            self.paths.state_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)
