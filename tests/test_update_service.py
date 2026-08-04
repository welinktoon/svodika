"""Tests for GitHub Releases update discovery and verified downloads."""

import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from services.update_service import (
    UpdateCancelled,
    UpdateFormatError,
    UpdateInfo,
    UpdateSecurityError,
    check_for_update,
    download_update,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, headers=None):
        super().__init__(payload)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def release_payload(version="1.2.0", include_checksum=True):
    installer_name = f"MeetingRecorderSetup-{version}.exe"
    assets = [
        {
            "name": installer_name,
            "browser_download_url": "https://example.test/installer",
            "size": 9,
        }
    ]
    if include_checksum:
        assets.append(
            {
                "name": f"{installer_name}.sha256",
                "browser_download_url": "https://example.test/checksum",
                "size": 80,
            }
        )
    return {
        "tag_name": f"v{version}",
        "name": f"Версия {version}",
        "body": "Исправления",
        "html_url": "https://example.test/release",
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }


def test_newer_release_is_returned():
    response = FakeResponse(json.dumps(release_payload()).encode())
    with patch("services.update_service._request", return_value=response):
        update = check_for_update("1.0.0")
    assert update is not None
    assert update.version == "1.2.0"
    assert update.installer_name == "MeetingRecorderSetup-1.2.0.exe"


def test_current_release_returns_none():
    response = FakeResponse(json.dumps(release_payload("1.0.0")).encode())
    with patch("services.update_service._request", return_value=response):
        assert check_for_update("1.0.0") is None


def test_release_without_checksum_is_rejected():
    response = FakeResponse(
        json.dumps(release_payload(include_checksum=False)).encode()
    )
    with patch("services.update_service._request", return_value=response):
        with pytest.raises(UpdateFormatError):
            check_for_update("1.0.0")


def test_verified_installer_download():
    payload = b"installer"
    checksum = hashlib.sha256(payload).hexdigest().encode()
    update = UpdateInfo(
        version="1.2.0",
        tag_name="v1.2.0",
        title="Version 1.2.0",
        notes="",
        release_url="",
        installer_name="MeetingRecorderSetup-1.2.0.exe",
        installer_url="https://example.test/installer",
        checksum_url="https://example.test/checksum",
        installer_size=len(payload),
    )
    responses = [
        FakeResponse(checksum + b"  MeetingRecorderSetup-1.2.0.exe"),
        FakeResponse(payload, {"Content-Length": str(len(payload))}),
    ]
    with tempfile.TemporaryDirectory() as directory, patch(
        "services.update_service._request", side_effect=responses
    ):
        target = download_update(update, destination_dir=Path(directory))
        assert target.read_bytes() == payload


def test_bad_installer_checksum_is_deleted():
    update = UpdateInfo(
        version="1.2.0",
        tag_name="v1.2.0",
        title="Version 1.2.0",
        notes="",
        release_url="",
        installer_name="MeetingRecorderSetup-1.2.0.exe",
        installer_url="https://example.test/installer",
        checksum_url="https://example.test/checksum",
        installer_size=3,
    )
    responses = [
        FakeResponse(b"0" * 64),
        FakeResponse(b"bad", {"Content-Length": "3"}),
    ]
    with tempfile.TemporaryDirectory() as directory, patch(
        "services.update_service._request", side_effect=responses
    ):
        with pytest.raises(UpdateSecurityError):
            download_update(update, destination_dir=Path(directory))
        assert not list(Path(directory).iterdir())

def test_cancelled_download_removes_partial_file():
    update = UpdateInfo(
        version="1.2.0",
        tag_name="v1.2.0",
        title="Version 1.2.0",
        notes="",
        release_url="",
        installer_name="MeetingRecorderSetup-1.2.0.exe",
        installer_url="https://example.test/installer",
        checksum_url="https://example.test/checksum",
        installer_size=3,
    )
    checksum = hashlib.sha256(b"abc").hexdigest().encode()
    responses = [FakeResponse(checksum), FakeResponse(b"abc")]
    with tempfile.TemporaryDirectory() as directory, patch(
        "services.update_service._request", side_effect=responses
    ):
        with pytest.raises(UpdateCancelled):
            download_update(
                update,
                destination_dir=Path(directory),
                is_cancelled=lambda: True,
            )
        assert not list(Path(directory).iterdir())
