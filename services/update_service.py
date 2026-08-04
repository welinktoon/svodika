"""GitHub Releases based application update checks and verified downloads."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from services.app_paths import get_app_data_dir
from version import GITHUB_RELEASES_URL, __version__


USER_AGENT = f"MeetingRecorder/{__version__}"
INSTALLER_PATTERN = re.compile(
    r"^MeetingRecorderSetup-(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\.exe$",
    re.IGNORECASE,
)


class UpdateError(RuntimeError):
    """Base error shown to the user when update handling fails."""


class UpdateFormatError(UpdateError):
    """A release is missing required installer metadata."""


class UpdateSecurityError(UpdateError):
    """A downloaded installer failed integrity verification."""


class UpdateCancelled(UpdateError):
    """The user canceled an in-progress installer download."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag_name: str
    title: str
    notes: str
    release_url: str
    installer_name: str
    installer_url: str
    checksum_url: str
    installer_size: int


def _version_key(value: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value or "")
    if not match:
        raise UpdateFormatError(f"Некорректная версия релиза: {value!r}")
    return tuple(int(part) for part in match.groups())


def _request(url: str, timeout: float):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Стабильные обновления пока не опубликованы.") from exc
        raise UpdateError(f"GitHub вернул ошибку HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(
            "Не удалось подключиться к GitHub. Проверьте интернет-соединение."
        ) from exc


def check_for_update(
    current_version: str = __version__,
    api_url: str = GITHUB_RELEASES_URL,
    timeout: float = 10.0,
) -> UpdateInfo | None:
    """Return the latest newer stable release, or ``None`` when current."""
    with _request(api_url, timeout) as response:
        try:
            release = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateFormatError("GitHub вернул повреждённые данные релиза.") from exc

    if release.get("draft") or release.get("prerelease"):
        return None

    tag_name = str(release.get("tag_name") or "")
    latest_key = _version_key(tag_name)
    if latest_key <= _version_key(current_version):
        return None

    installer = None
    checksum = None
    assets = release.get("assets") or []
    for asset in assets:
        name = str(asset.get("name") or "")
        if INSTALLER_PATTERN.match(name):
            installer = asset
            break

    if installer is None:
        raise UpdateFormatError(
            "В релизе нет Windows-установщика MeetingRecorderSetup-*.exe."
        )

    checksum_name = f"{installer['name']}.sha256"
    for asset in assets:
        if str(asset.get("name") or "").lower() == checksum_name.lower():
            checksum = asset
            break

    if checksum is None:
        raise UpdateFormatError(
            "В релизе нет контрольной суммы установщика. "
            "Обновление отменено для безопасности."
        )

    return UpdateInfo(
        version=".".join(str(part) for part in latest_key),
        tag_name=tag_name,
        title=str(release.get("name") or tag_name),
        notes=str(release.get("body") or "").strip(),
        release_url=str(release.get("html_url") or ""),
        installer_name=Path(str(installer["name"])).name,
        installer_url=str(installer.get("browser_download_url") or ""),
        checksum_url=str(checksum.get("browser_download_url") or ""),
        installer_size=int(installer.get("size") or 0),
    )


def download_update(
    update: UpdateInfo,
    *,
    destination_dir: Path | None = None,
    progress: Callable[[int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download an installer and require its release SHA-256 checksum."""
    target_dir = destination_dir or (get_app_data_dir() / "updates")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(update.installer_name).name
    partial = target.with_suffix(target.suffix + ".part")

    with _request(update.checksum_url, timeout) as response:
        checksum_text = response.read().decode("ascii", errors="strict").strip()
    expected = checksum_text.split()[0].lower() if checksum_text else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateFormatError("Контрольная сумма релиза имеет неверный формат.")

    digest = hashlib.sha256()
    downloaded = 0
    total = max(0, update.installer_size)
    try:
        with _request(update.installer_url, timeout) as response, partial.open("wb") as handle:
            response_total = int(response.headers.get("Content-Length") or 0)
            total = total or response_total
            while True:
                if is_cancelled and is_cancelled():
                    raise UpdateCancelled("Загрузка обновления отменена.")
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress and total:
                    progress(min(100, int(downloaded * 100 / total)))
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    actual = digest.hexdigest().lower()
    if not hmac.compare_digest(actual, expected):
        partial.unlink(missing_ok=True)
        raise UpdateSecurityError(
            "Контрольная сумма не совпала. Установщик удалён."
        )

    partial.replace(target)
    if progress:
        progress(100)
    return target
