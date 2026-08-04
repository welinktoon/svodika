"""
Transcription backends for the OpenWhisper application.
"""
from .base import TranscriptionBackend

__all__ = ['TranscriptionBackend', 'LocalWhisperBackend', 'OpenAIBackend']


def __getattr__(name):
    """Load heavyweight backend modules only when the caller requests them."""
    if name == "LocalWhisperBackend":
        from .local_backend import LocalWhisperBackend

        return LocalWhisperBackend
    if name == "OpenAIBackend":
        from .openai_backend import OpenAIBackend

        return OpenAIBackend
    raise AttributeError(name)
