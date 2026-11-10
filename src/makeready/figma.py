"""Figma REST client and a version-keyed file cache."""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.figma.com/v1"


class FigmaError(Exception):
    """A Figma request failed or the response was not the shape requested."""


class FigmaClient:
    def __init__(self, token: str | None, opener=urllib.request.urlopen):
        if not isinstance(token, str) or not token:
            raise FigmaError("no Figma token: set figma.token in the configuration")
        self._token = token
        self._opener = opener

    def get_file(self, key: str, version: str | None = None) -> dict:
        params = [("geometry", "paths")]
        if version is not None:
            params.append(("version", version))
        return self._json(f"{API}/files/{key}?{urllib.parse.urlencode(params)}")

    def render(
        self,
        key: str,
        ids: list[str],
        fmt: str,
        version: str,
        scale: float = 1,
    ) -> dict[str, str | None]:
        params = [
            ("ids", ",".join(ids)),
            ("format", fmt),
            ("version", version),
            ("scale", _scale_param(scale)),
        ]
        url = f"{API}/images/{key}?{urllib.parse.urlencode(params)}"
        images = _object(self._json(url), url, "images")
        result: dict[str, str | None] = {}
        for node_id, image_url in images.items():
            if not isinstance(node_id, str):
                raise FigmaError(f"{url}: images keys must be text")
            if image_url is not None and not isinstance(image_url, str):
                raise FigmaError(f"{url}: images.{node_id} must be text or null")
            result[node_id] = image_url
        return result

    def image_fills(self, key: str) -> dict[str, str]:
        url = f"{API}/files/{key}/images"
        meta = _object(self._json(url), url, "meta")
        images = _object(meta, url, "images", "meta.images")
        result: dict[str, str] = {}
        for ref, image_url in images.items():
            if not isinstance(ref, str) or not isinstance(image_url, str):
                raise FigmaError(f"{url}: meta.images entries must be text")
            result[ref] = image_url
        return result

    def download(self, url: str, dest: Path) -> None:
        payload = self._read(url, token=False)
        if not isinstance(payload, (bytes, bytearray)):
            raise FigmaError(f"{url}: response body must be bytes")
        dest = Path(dest)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
        except OSError as error:
            raise FigmaError(f"{dest}: {error}") from error

    def _json(self, url: str) -> dict:
        payload = self._read(url, token=True)
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise FigmaError(f"{url}: {error}") from error
        if not isinstance(data, dict):
            raise FigmaError(f"{url}: response must be a JSON object")
        return data

    def _read(self, url: str, token: bool) -> bytes:
        headers = {"X-Figma-Token": self._token} if token else {}
        request = urllib.request.Request(url, headers=headers)
        try:
            with self._opener(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise _http_error(error) from error
        except (urllib.error.URLError, OSError) as error:
            raise FigmaError(f"{url}: {error}") from error


def cached_file(client: FigmaClient, key: str, cache_dir: Path, version: str | None = None) -> dict:
    cache_dir = Path(cache_dir)
    if version is not None:
        if not isinstance(version, str) or not version or not _safe_version(version):
            raise FigmaError(f"Figma file {key}: version must be text")
        cached = cache_dir / f"{version}.json"
        if cached.is_file():
            return _read_cached(cached)
    doc = client.get_file(key, version)
    if not isinstance(doc, dict):
        raise FigmaError(f"Figma file {key}: response must be a JSON object")
    found = doc.get("version")
    if not isinstance(found, str) or not found or not _safe_version(found):
        raise FigmaError(f"Figma file {key}: version must be text")
    path = cache_dir / f"{found}.json"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))
    except OSError as error:
        raise FigmaError(f"{path}: {error}") from error
    return doc


def _read_cached(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        raise FigmaError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise FigmaError(f"{path}: response must be a JSON object")
    return data


def _object(data: dict, url: str, key: str, label: str | None = None) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise FigmaError(f"{url}: {label or key} must be an object")
    return value


def _http_error(error: urllib.error.HTTPError) -> FigmaError:
    code = error.code
    if code == 403:
        return FigmaError(
            "Figma rejected the token (HTTP 403); it may have expired or lack access to the file"
        )
    if code == 429:
        return FigmaError(
            "Figma rate limit reached (HTTP 429); file and image calls need a Full or Dev seat"
        )
    return FigmaError(f"Figma request failed (HTTP {code})")


def _scale_param(scale: float) -> str:
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise FigmaError("scale must be a number")
    number = float(scale)
    if number.is_integer():
        return str(int(number))
    return format(number, "g")


def _safe_version(version: str) -> bool:
    return "/" not in version and version not in (".", "..") and "\\" not in version
