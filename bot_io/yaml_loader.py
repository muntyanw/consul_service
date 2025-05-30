"""yaml_loader.py
~~~~~~~~~~~~~~~~~~
Utility module that scans a directory for user YAML configuration files, validates
and converts them into typed ``UserConfig`` objects.  The module deliberately
contains **no** UI logic – it is pure I/O + validation and can therefore be
unit‑tested in isolation.

The YAML schema corresponds to the requirements described in the technical
specification for the e‑consul booking bot.

Author: chatGPT‑assistant (2025‑05‑30)
Language: English / Ukrainian comments (no Russian)
"""
from __future__ import annotations

import datetime as _dt
from utils.logger import setup_logger
import pathlib as _pl
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import yaml  # PyYAML
from cryptography.fernet import Fernet, InvalidToken

__all__ = [
    "UserConfig",
    "YAMLLoader",
    "ConfigError",
]

LOGGER = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class ConfigError(RuntimeError):
    """Raised when a YAML file is syntactically correct but semantically invalid."""


# ---------------------------------------------------------------------------
# Dataclass representing a single user entry
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class UserConfig:
    """Typed representation of one YAML‑file record.

    All date/time values are stored as *aware* Python objects in UTC.
    """

    alias: str                          # derived from file name if missing
    key_path: _pl.Path                  # absolute path to the *key* file
    key_issuer: Optional[str]           # may be None for auto‑detect
    key_password: str                   # decrypted plaintext password

    birthdate: _dt.date                 # ISO‑formatted yyyy‑mm‑dd
    gender: str                         # “Чоловіча” / “Жіноча” or raw text on the site

    country: str
    consulates: Sequence[str]
    service: str

    surname: Optional[str]
    name: Optional[str]
    patronymic: Optional[str]           # None → «для себе» / missing patronymic
    
    source_file: _pl.Path = field(repr=False, compare=False)

    min_date: Optional[_dt.date] = None  # absolute floor date
    relative_days: Optional[int] = None  # days from *now*

    

    # ---------------------------------------------------------------------
    # Helper API
    # ---------------------------------------------------------------------
    @property
    def earliest_allowed(self) -> _dt.date:
        """Return the earliest date this user can accept for appointment."""
        if self.min_date:
            return self.min_date
        if self.relative_days is not None:
            return (_dt.date.today() + _dt.timedelta(days=self.relative_days))
        # If both are None – today is acceptable
        return _dt.date.today()


# ---------------------------------------------------------------------------
# YAML Loader
# ---------------------------------------------------------------------------
class YAMLLoader:
    """Scans a directory for ``*.yaml`` configs and yields :class:`UserConfig`."""

    REQUIRED_FIELDS = {
        "key_path",
        "birthdate",
        "gender",
        "country",
        "consulates",
        "service",
    }

    def __init__(self, users_dir: _pl.Path, keys_base: _pl.Path | None = None):
        self.users_dir = users_dir.expanduser().resolve()
        self.keys_base = (keys_base.expanduser().resolve() if keys_base else None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(self) -> List[UserConfig]:
        """Load and validate every ``*.yaml`` file in *users_dir*.

        Invalid files are logged as warnings and ignored; at least one valid
        config must remain, otherwise :class:`ConfigError` is raised.
        """
        if not self.users_dir.is_dir():
            raise ConfigError(f"Config directory does not exist: {self.users_dir}")

        configs: list[UserConfig] = []
        for path in sorted(self.users_dir.glob("*.yaml")):
            try:
                conf = self._parse_file(path)
            except Exception as exc:
                LOGGER.warning("Skip invalid config %s: %s", path.name, exc)
                continue
            configs.append(conf)

        if not configs:
            raise ConfigError("No valid user configurations found.")
        return configs

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _parse_file(self, path: _pl.Path) -> UserConfig:
        with path.open("rt", encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh) or {}

        missing = self.REQUIRED_FIELDS - raw.keys()
        if missing:
            raise ConfigError(f"Missing required fields: {', '.join(sorted(missing))}")

        alias = raw.get("alias") or path.stem

        # --- key path ---------------------------------------------------
        key_path = _pl.Path(raw["key_path"])
        if not key_path.is_absolute() and self.keys_base:
            key_path = self.keys_base / key_path
        key_path = key_path.expanduser().resolve()
        if not key_path.exists():
            raise ConfigError(f"Key file not found: {key_path}")

        # --- decrypt password ------------------------------------------
        encrypted = raw.get("key_password")
        if encrypted is None:
            raise ConfigError("Field 'key_password' missing")
        try:
            password = self._decrypt_password(encrypted)
        except InvalidToken as exc:
            raise ConfigError("Unable to decrypt key_password – invalid token") from exc

        # --- birthdate --------------------------------------------------
        try:
            birthdate = _dt.date.fromisoformat(raw["birthdate"])
        except (TypeError, ValueError):
            raise ConfigError("birthdate must be ISO yyyy-mm-dd")

        # --- gender -----------------------------------------------------
        gender = str(raw["gender"]).strip()

        # --- country / consulates / service ----------------------------
        country = str(raw["country"]).strip()
        cons = raw["consulates"]
        if not isinstance(cons, list) or not cons:
            raise ConfigError("consulates must be a non-empty list")
        consulates: list[str] = [str(i).strip() for i in cons]
        service = str(raw["service"]).strip()

        # --- client name ------------------------------------------------
        cn = raw.get("client_name", {}) or {}
        surname = cn.get("surname")
        name = cn.get("name")
        patronymic = cn.get("patronymic")

        # --- date restrictions -----------------------------------------
        min_date = None
        rel = None
        if (md := raw.get("min_date")) and isinstance(md, str):
            try:
                min_date = _dt.date.fromisoformat(md)
            except ValueError:
                raise ConfigError("min_date must be ISO yyyy-mm-dd")
        elif (rel_days := raw.get("days_from_now")) is not None:
            try:
                rel = int(rel_days)
                if rel < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ConfigError("days_from_now must be non-negative int")

        return UserConfig(
            alias=alias,
            key_path=key_path,
            key_issuer=raw.get("key_issuer"),
            key_password=password,
            birthdate=birthdate,
            gender=gender,
            country=country,
            consulates=consulates,
            service=service,
            surname=surname,
            name=name,
            patronymic=patronymic,
            min_date=min_date,
            relative_days=rel,
            source_file=path,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _decrypt_password(token: str) -> str:
        """Decrypt a Fernet token string to plaintext."""
        # NOTE: Production code would keep the **key** in an env var / vault.
        fernet_key_env = "FERNET_SECRET_KEY"
        from os import getenv

        key = getenv(fernet_key_env)
        if not key:
            raise ConfigError(
                f"Environment variable {fernet_key_env} not set – cannot decrypt passwords"
            )
        f = Fernet(key.encode())
        return f.decrypt(token.encode()).decode("utf-8")


# ---------------------------------------------------------------------------
# Minimal demonstration when run directly
# ---------------------------------------------------------------------------
def main() -> None:  # pragma: no cover
    import argparse, json, sys

    parser = argparse.ArgumentParser(description="Validate and show YAML configs")
    parser.add_argument("path", help="Directory containing user *.yaml files")
    parser.add_argument("--keys-base", help="Base directory for relative key paths")
    args = parser.parse_args()

    logging.basicConfig(level=_logging.INFO, format="%(levelname)s: %(message)s")

    try:
        loader = YAMLLoader(_pl.Path(args.path), _pl.Path(args.keys_base) if args.keys_base else None)
        cfgs = loader.load()
    except ConfigError as e:
        LOGGER.error("Config error: %s", e)
        sys.exit(1)

    # Pretty‑print result as JSON for visual inspection
    data = [cfg.__dict__ for cfg in cfgs]
    print(json.dumps(data, default=str, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
