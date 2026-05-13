"""City configuration loading and validation.

Reads `config/cities/<slug>.yaml`, validates against
`config/cities/__schema__.yaml`, and returns a typed config object.
Misconfigured cities fail loudly here rather than corrupting downstream
state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config" / "cities"


@dataclass(frozen=True, slots=True)
class CityConfig:
    name: str
    bbox: tuple[float, float, float, float]
    geofabrik_extract_url: str
    local_cache_path: Path
    default: bool
    display_name: str | None

    @property
    def resolved_cache_path(self) -> Path:
        """Absolute path to the cached PBF, relative to repo root."""
        if self.local_cache_path.is_absolute():
            return self.local_cache_path
        return REPO_ROOT / self.local_cache_path


def _load_schema(config_dir: Path) -> Draft202012Validator:
    schema_path = config_dir / "__schema__.yaml"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_city(name: str, config_dir: Path = DEFAULT_CONFIG_DIR) -> CityConfig:
    """Load and validate the config for `name`.

    Raises:
        FileNotFoundError: when no such config exists.
        jsonschema.ValidationError: when the file violates the JSON Schema.
    """
    path = config_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"City config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    validator = _load_schema(config_dir)
    validator.validate(raw)

    bbox = tuple(raw["bbox"])
    if len(bbox) != 4:
        raise ValueError(f"bbox must have exactly 4 elements, got {len(bbox)}")
    bbox_tuple: tuple[float, float, float, float] = (
        float(bbox[0]),
        float(bbox[1]),
        float(bbox[2]),
        float(bbox[3]),
    )

    return CityConfig(
        name=raw["name"],
        bbox=bbox_tuple,
        geofabrik_extract_url=raw["geofabrik_extract_url"],
        local_cache_path=Path(raw["local_cache_path"]),
        default=bool(raw.get("default", False)),
        display_name=raw.get("display_name"),
    )


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Source .env or copy .env.example to .env.")
    return url
