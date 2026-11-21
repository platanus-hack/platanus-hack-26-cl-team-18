"""Filesystem media cache, served by the app at /media (StaticFiles)."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_KEY = re.compile(r"^[a-f0-9]{16,64}$")
_EXT = re.compile(r"^[a-z0-9]{2,5}$")


class FileMediaStore:
    def __init__(
        self, root: Path, *, subdir: str = "voiceovers", url_prefix: str = "/media"
    ) -> None:
        self._dir = root / subdir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._url = f"{url_prefix.rstrip('/')}/{subdir}"

    def _path(self, key: str, extension: str) -> Path:
        if not _KEY.match(key) or not _EXT.match(extension):
            raise ValueError("invalid media key or extension")  # no path traversal, ever
        return self._dir / f"{key}.{extension}"

    def url_if_cached(self, key: str, extension: str) -> str | None:
        path = self._path(key, extension)
        return f"{self._url}/{path.name}" if path.is_file() and path.stat().st_size > 0 else None

    def put(self, key: str, extension: str, data: bytes) -> str:
        if not data:
            raise ValueError("refusing to cache empty media")
        path = self._path(key, extension)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".tmp-", suffix=f".{extension}")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)  # atomic: readers never see a partial file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return f"{self._url}/{path.name}"
