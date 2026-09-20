"""Configuration loading.

Everything is optional. With no ``config.json`` at all the app still runs and can
parse a Revolut CSV and export it; filling in Monzo credentials unlocks importing
straight from Monzo.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.json"

# Values copied straight out of config.example.json count as "not filled in".
_PLACEHOLDER_MARKERS = ("YOUR_", "/path/to/")

DEFAULT_REDIRECT_URI = "http://localhost:8501/"

# Monzo timestamps are UTC; dates are shown in this zone so a late-night
# purchase lands on the day you actually made it.
DEFAULT_TIMEZONE = "Europe/London"
DEFAULT_MONZO_TOKEN_PATH = "config/monzo-token.json"

DEFAULT_CATEGORIES: list[str] = [
    "", "Groceries", "Food & Drinks", "General Shopping", "Transportation",
    "Entertainment", "Subscriptions", "Hobbies", "Miscellaneous",
    "Holidays", "Gifts",
]

DEFAULT_MONZO_CATEGORY_MAP: dict[str, str] = {
    "groceries": "Groceries",
    "eating_out": "Food & Drinks",
    "shopping": "General Shopping",
    "transport": "Transportation",
    "entertainment": "Entertainment",
    "gifts": "Gifts",
    "holidays": "Holidays",
}


class ConfigError(Exception):
    """Raised when config.json exists but cannot be read."""


@dataclass(frozen=True)
class Config:
    # Blank credentials mean the Monzo integration is switched off.
    monzo_client_id: str = ""
    monzo_client_secret: str = ""
    monzo_token_path: str = str(_REPO_ROOT / DEFAULT_MONZO_TOKEN_PATH)

    # Must exactly match the redirect URI registered on developers.monzo.com.
    redirect_uri: str = DEFAULT_REDIRECT_URI

    timezone: str = DEFAULT_TIMEZONE

    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    monzo_category_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MONZO_CATEGORY_MAP))

    @property
    def monzo_enabled(self) -> bool:
        """True when a Monzo developer app has been configured."""
        return bool(self.monzo_client_id and self.monzo_client_secret)


def _clean(data: dict, key: str) -> str:
    """A configured string value, or "" if absent or still an example placeholder."""
    value = str(data.get(key, "") or "").strip()
    if any(marker in value for marker in _PLACEHOLDER_MARKERS):
        return ""
    return value


def _resolve(path_value: str) -> str:
    """Resolve a config path, treating relative paths as relative to the repo root."""
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return str(path)


def load_config(config_path: Path | None = None) -> Config:
    """Load config.json if it exists, falling back to defaults for anything absent."""
    path = config_path or _CONFIG_PATH
    if not path.exists():
        return Config()
    try:
        with path.open() as fh:
            data: dict = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"config.json could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config.json must contain a JSON object.")

    return Config(
        monzo_client_id=_clean(data, "monzo_client_id"),
        monzo_client_secret=_clean(data, "monzo_client_secret"),
        monzo_token_path=_resolve(_clean(data, "monzo_token_path") or DEFAULT_MONZO_TOKEN_PATH),
        redirect_uri=_clean(data, "redirect_uri") or DEFAULT_REDIRECT_URI,
        timezone=_clean(data, "timezone") or DEFAULT_TIMEZONE,
        categories=list(data.get("categories", DEFAULT_CATEGORIES)),
        monzo_category_map=dict(data.get("monzo_category_map", DEFAULT_MONZO_CATEGORY_MAP)),
    )
