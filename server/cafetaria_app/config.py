"""Configuration loading for the cafetaria server."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


DEFAULTS: Dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8080,
        "domain": "localhost",
        "secret_key": "",
        "log_level": "INFO",
    },
    "cafetaria": {
        "url": "https://webparent.paiementdp.com/aliAuthentification.php?site=aes00152",
        "username": None,
        "password": None,
        "low_credit_threshold": 10.0,
    },
    "fetch_frequency_minutes": 60,
    "database": "./data/cafetaria.sqlite",
    "users": [],
}


class ConfigError(RuntimeError):
    """Raised when the configuration file is missing or invalid."""


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def find_config_path(explicit: Optional[str] = None) -> Path:
    """Locate config.yml: explicit path, CAFETARIA_CONFIG env var, or well-known spots."""
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get("CAFETARIA_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    # Repo layout: <root>/config.yml with server code in <root>/server
    here = Path(__file__).resolve().parent  # .../server/cafetaria_app
    candidates.append(here.parent.parent / "config.yml")  # <root>/config.yml
    candidates.append(Path.cwd() / "config.yml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ConfigError(
        "Configuration file not found. Looked at: "
        + ", ".join(str(c) for c in candidates)
    )


def load_config(explicit: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate configuration from the YAML file."""
    path = find_config_path(explicit)
    try:
        with open(path, "r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML configuration {path}: {exc}") from exc

    config = _deep_merge(DEFAULTS, raw)

    cafetaria = config["cafetaria"]
    if not cafetaria.get("username") or not cafetaria.get("password"):
        raise ConfigError(
            "Missing required 'cafetaria.username' / 'cafetaria.password'"
        )
    if not cafetaria.get("url", "").startswith(("http://", "https://")):
        raise ConfigError("'cafetaria.url' must be an absolute http(s) URL")

    users = config.get("users") or []
    if not users:
        raise ConfigError("At least one application user must be defined in 'users'")

    secret = (config["server"].get("secret_key") or "").strip()
    if not secret or secret == DEFAULTS["server"]["secret_key"]:
        raise ConfigError(
            "'server.secret_key' must be set to a strong random value in config.yml"
        )

    # Resolve relative database path against the config file location.
    db_path = Path(config["database"])
    if not db_path.is_absolute():
        db_path = (path.parent / db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config["database"] = str(db_path)

    try:
        freq = int(config["fetch_frequency_minutes"])
    except (TypeError, ValueError):
        raise ConfigError("'fetch_frequency_minutes' must be a number of minutes")
    if freq < 1:
        raise ConfigError("'fetch_frequency_minutes' must be >= 1")
    config["fetch_frequency_minutes"] = freq

    config["_config_path"] = str(path)
    return config
