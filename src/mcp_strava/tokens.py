"""The token file: Strava app credentials plus the OAuth tokens they unlock.

Strava rotates refresh tokens, so the newest one must always be written back.
Writes go to a temp file in the same directory and are swapped in with
os.replace, so a crash mid-write never leaves a half-written file behind.
"""

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_TOKEN_FILE = Path("~/.config/mcp-strava/tokens.json")


def token_path(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("STRAVA_TOKEN_FILE") or DEFAULT_TOKEN_FILE).expanduser()


class TokenFileError(Exception):
    """The token file exists but does not hold a JSON object."""


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        """The stored fields, or {} when the file does not exist yet."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
            raise TokenFileError(f"{self.path} is not valid JSON ({exc}).") from exc
        if not isinstance(data, dict):
            raise TokenFileError(f"{self.path} does not hold a JSON object.")
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkstemp creates the file 0600, so tokens are never readable by other users, even briefly.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tokens-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dict(data), f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
