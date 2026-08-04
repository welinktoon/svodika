"""Recognition-language settings are applied to every transcription backend."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from transcriber.base import TranscriptionBackend
from transcriber.local_backend import LocalWhisperBackend
from transcriber.openai_backend import OpenAIBackend
from services.settings import resolve_transcription_language


def test_language_resolver_defaults_to_russian_and_rejects_unknown_values():
    assert resolve_transcription_language({}) == "ru"
    assert resolve_transcription_language({"transcription_language": "en"}) == "en"
    assert resolve_transcription_language({"transcription_language": "de"}) == "ru"


def test_local_backend_passes_selected_language_to_whisper():
    model = Mock()
    model.transcribe.return_value = (
        iter([SimpleNamespace(text=" hello ")]),
        SimpleNamespace(language="en", language_probability=0.99),
    )
    backend = LocalWhisperBackend.__new__(LocalWhisperBackend)
    TranscriptionBackend.__init__(backend)
    backend.model = model

    with patch(
        "services.settings.resolve_transcription_language",
        return_value="en",
    ):
        transcript = backend._transcribe_file_once("meeting.wav", None)

    assert transcript == "hello"
    assert model.transcribe.call_args.kwargs["language"] == "en"


def test_openai_backend_passes_selected_language(tmp_path: Path):
    audio_path = tmp_path / "meeting.wav"
    audio_path.write_bytes(b"RIFF")
    backend = OpenAIBackend.__new__(OpenAIBackend)
    TranscriptionBackend.__init__(backend)
    backend.model_type = "api_whisper"
    backend.api_key = "test-key"
    backend.client = Mock()
    backend.client.audio.transcriptions.create.return_value = " transcript "

    with patch(
        "transcriber.openai_backend.resolve_transcription_language",
        return_value="en",
    ):
        transcript = backend.transcribe(str(audio_path))

    assert transcript == "transcript"
    assert (
        backend.client.audio.transcriptions.create.call_args.kwargs["language"]
        == "en"
    )
