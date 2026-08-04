"""Unit tests for local Whisper CUDA failure recovery."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from transcriber.base import TranscriptionBackend
from transcriber.local_backend import LocalWhisperBackend
from services.settings import SettingsKey


class _CudaFailureDuringIteration:
    def transcribe(self, *_args, **_kwargs):
        def segments():
            raise RuntimeError("CUDA failed with error unknown error")
            yield  # pragma: no cover

        return segments(), SimpleNamespace(
            language="en",
            language_probability=0.99,
        )


class _WorkingCpuModel:
    def transcribe(self, *_args, **_kwargs):
        return iter(
            [
                SimpleNamespace(text=" hello"),
                SimpleNamespace(text=" world "),
            ]
        ), SimpleNamespace(language="en", language_probability=0.98)


def _make_backend(model):
    backend = LocalWhisperBackend.__new__(LocalWhisperBackend)
    TranscriptionBackend.__init__(backend)
    backend.model_name = "small"
    backend.model = model
    backend._device = "cuda"
    backend._compute_type = "float16"
    backend._override_device = None
    backend._override_compute_type = None
    backend._model_missing = False
    backend._last_loaded_model = "small"
    backend.cleanup = Mock(side_effect=lambda: setattr(backend, "model", None))
    backend._select_best_compute_type = Mock(return_value="int8")
    return backend


def test_cuda_inference_failure_retries_once_on_cpu():
    backend = _make_backend(_CudaFailureDuringIteration())
    cpu_model = _WorkingCpuModel()

    with patch(
        "transcriber.local_backend.WhisperModel",
        return_value=cpu_model,
    ) as model_class:
        transcript = backend.transcribe("meeting.wav")

    assert transcript == "hello world"
    assert backend._device == "cpu"
    assert backend._compute_type == "int8"
    assert backend.model is cpu_model
    model_class.assert_called_once_with(
        "small",
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )


def test_non_cuda_error_is_not_hidden_by_cpu_fallback():
    backend = _make_backend(Mock())
    backend.model.transcribe.side_effect = ValueError("invalid audio")

    with patch("transcriber.local_backend.WhisperModel") as model_class:
        with pytest.raises(ValueError, match="invalid audio"):
            backend.transcribe("broken.wav")

    model_class.assert_not_called()
    assert backend._device == "cuda"


def test_forced_cuda_without_nvidia_falls_back_to_cpu():
    backend = LocalWhisperBackend(
        model_name="turbo",
        device="cuda",
        compute_type="float16",
        autoload=False,
    )
    backend._cuda_is_available = Mock(return_value=False)
    backend._get_supported_compute_types = Mock(return_value={"int8", "float32"})

    with patch(
        "services.settings.settings_manager.load_all_settings",
        return_value={SettingsKey.WHISPER_MODEL: "turbo"},
    ):
        device, compute_type, model = backend._detect_hardware()

    assert device == "cpu"
    assert compute_type == "int8"
    assert model == "turbo"
