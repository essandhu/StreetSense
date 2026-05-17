"""City configuration loading and validation.

Reads `config/cities/<slug>.yaml`, validates against
`config/cities/__schema__.yaml`, and returns a typed config object.
Misconfigured cities fail loudly here rather than corrupting downstream
state.

Phase 4b: extended to read `slug`, `default_zoom`, `timezone`, and
optional `notes` (ADR 0010). The `CityConfig` dataclass now exposes
these alongside the existing Phase 1 fields, plus a `to_city()` helper
that converts to the API/DB-side `api.schemas.City` Pydantic model
used by the seed-cities loader (Task 1.6) and the `GET /api/cities`
endpoint (Task 3.4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from api.schemas import City

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config" / "cities"


@dataclass(frozen=True, slots=True)
class CityConfig:
    # Phase 1 fields ------------------------------------------------------
    name: str
    bbox: tuple[float, float, float, float]
    geofabrik_extract_url: str
    local_cache_path: Path
    default: bool
    display_name: str | None

    # Phase 4b fields -----------------------------------------------------
    slug: str
    default_zoom: int
    timezone: str
    notes: str | None

    @property
    def resolved_cache_path(self) -> Path:
        """Absolute path to the cached PBF, relative to repo root."""
        if self.local_cache_path.is_absolute():
            return self.local_cache_path
        return REPO_ROOT / self.local_cache_path

    def to_city(self) -> City:
        """Convert this YAML-shape config to the API/DB-shape City model.

        The Pydantic model performs the boundary validation (IANA
        timezone lookup, bbox range checks); the YAML JSON Schema
        catches structural issues before we get here.
        """
        from api.schemas import City  # local import to avoid circular

        return City(
            id=None,
            slug=self.slug,
            name=self.display_name or self.slug,
            bbox=self.bbox,
            default_zoom=self.default_zoom,
            timezone=self.timezone,
        )


def _load_schema(config_dir: Path) -> Draft202012Validator:
    schema_path = config_dir / "__schema__.yaml"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_city(name: str, config_dir: Path = DEFAULT_CONFIG_DIR) -> CityConfig:
    """Load and validate the config for `name`.

    `name` here is the YAML filename stem (== `slug`, per Phase 4b).

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
        # Phase 4b — all three are required by the JSON Schema, so no
        # defaults here. notes stays optional.
        slug=raw["slug"],
        default_zoom=int(raw["default_zoom"]),
        timezone=raw["timezone"],
        notes=raw.get("notes"),
    )


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Source .env or copy .env.example to .env.")
    return url
