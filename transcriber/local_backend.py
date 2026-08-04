"""
Local Whisper transcription backend using faster-whisper for optimized performance.

This backend uses faster-whisper (CTranslate2) which provides:
- Up to 4x faster transcription than openai-whisper
- Lower memory usage through quantization
- Built-in VAD (Voice Activity Detection) for silence skipping
- No external FFmpeg dependency (uses PyAV)
"""
import logging
from typing import Any, Callable, Optional, List, Tuple
from .base import TranscriptionBackend
from config import config

logger = logging.getLogger(__name__)

# Kept as a patchable module attribute for tests and integrations, while the
# actual faster-whisper package remains lazily imported.
WhisperModel = None


def _get_whisper_model_class():
    global WhisperModel
    if WhisperModel is None:
        from faster_whisper import WhisperModel as model_class

        WhisperModel = model_class
    return WhisperModel


class LocalWhisperBackend(TranscriptionBackend):
    """Local Whisper model transcription backend using faster-whisper."""

    def __init__(
        self,
        model_name: str = None,
        device: str = None,
        compute_type: str = None,
        autoload: bool = True,
    ):
        """Initialize the local faster-whisper backend.

        Args:
            model_name: Whisper model name to use. Reads from settings if None.
                       Use "auto" to auto-select based on hardware (turbo for GPU, base for CPU).
                       Available: auto, tiny, base, small, medium, large-v2, large-v3, turbo, distil-large-v3
            device: Device to use ("cuda" or "cpu"). Overrides settings if provided.
            compute_type: Compute type to use ("float16", "float32", "int8", etc.). Overrides settings if provided.
        """
        super().__init__()
        # Read model from settings if not explicitly provided
        if model_name is None:
            from services.settings import SettingsKey, settings_manager
            settings = settings_manager.load_all_settings()
            model_name = settings.get(SettingsKey.WHISPER_MODEL, config.DEFAULT_WHISPER_MODEL)
        self.model_name = model_name  # May be "auto", resolved in _load_model
        self.model: Optional[Any] = None
        self._device: Optional[str] = None
        self._compute_type: Optional[str] = None
        self._override_device = device  # Store override values
        self._override_compute_type = compute_type
        self._model_missing = False
        self._last_loaded_model: Optional[str] = None
        if autoload:
            self._load_model()

    def _cuda_is_available(self) -> bool:
        """Check whether a usable CUDA device is present.

        Uses CTranslate2's own device probe rather than torch: CTranslate2 is a
        hard dependency of this backend, whereas torch is not installed at all,
        so the previous ``import torch`` check always reported no GPU and forced
        CPU even on CUDA-capable machines.

        Returns:
            True if at least one CUDA device is available, False otherwise.
        """
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception as e:
            logger.debug(f"CUDA availability probe failed, assuming CPU: {e}")
            return False

    def _get_supported_compute_types(self, device: str) -> set:
        """Get compute types supported by the current hardware.

        Args:
            device: "cpu" or "cuda"

        Returns:
            Set of supported compute type strings.
        """
        try:
            import ctranslate2
            supported = ctranslate2.get_supported_compute_types(device)
            logger.debug(f"Supported compute types for {device}: {supported}")
            return set(supported)
        except Exception as e:
            logger.warning(f"Could not query supported compute types: {e}")
            # Return safe fallback - float32 is always supported
            return {"float32"}

    def _select_best_compute_type(self, device: str, preferred: str) -> str:
        """Select the best available compute type, with fallback.

        Args:
            device: "cpu" or "cuda"
            preferred: The preferred compute type to use if supported

        Returns:
            The best available compute type string.
        """
        supported = self._get_supported_compute_types(device)

        if preferred in supported:
            return preferred

        # Preferred type not supported, try fallbacks
        if device == "cpu":
            # CPU fallback order: int8 (fastest) -> int8_float32 -> float32 (most compatible)
            fallback_order = ["int8", "int8_float32", "float32"]
        else:
            # GPU fallback order: float16 (fastest) -> int8_float16 -> float32
            fallback_order = ["float16", "int8_float16", "float32"]

        for fallback in fallback_order:
            if fallback in supported:
                logger.warning(
                    f"Compute type '{preferred}' not supported on this {device}. "
                    f"Falling back to '{fallback}'. "
                    f"(Supported types: {', '.join(sorted(supported))})"
                )
                return fallback

        # Ultimate fallback - float32 should always work
        logger.warning(f"No preferred compute types available, using float32")
        return "float32"

    def _detect_hardware(self) -> Tuple[str, str, str]:
        """Auto-detect the best device, compute type, and model for transcription.

        Returns:
            Tuple of (device, compute_type, model) where:
            - device: "cuda" for GPU or "cpu" for CPU
            - compute_type: "float16" for GPU, "int8" for CPU (if supported)
            - model: "turbo" for GPU, "base" for CPU
        """
        # Use override values if provided, otherwise check user settings
        from services.settings import SettingsKey, settings_manager
        settings = settings_manager.load_all_settings()

        if self._override_device is not None:
            device = self._override_device
        else:
            device = settings.get(SettingsKey.WHISPER_DEVICE, config.FASTER_WHISPER_DEVICE)

        if self._override_compute_type is not None:
            compute_type = self._override_compute_type
        else:
            compute_type = settings.get(SettingsKey.WHISPER_COMPUTE_TYPE, config.FASTER_WHISPER_COMPUTE_TYPE)

        # Get model from settings (no override for model, use model_name parameter instead)
        model = settings.get(SettingsKey.WHISPER_MODEL, config.DEFAULT_WHISPER_MODEL)

        # Auto-detect based on CUDA availability
        has_cuda = False
        if device == "auto" or compute_type == "auto" or model == "auto":
            has_cuda = self._cuda_is_available()

            if has_cuda:
                detected_device = "cuda"
                detected_compute = "float16"
                detected_model = "turbo"
                logger.info("CUDA detected - using GPU acceleration with float16 and turbo model")
            else:
                detected_device = "cpu"
                detected_compute = "int8"
                detected_model = "base"
                logger.info("No CUDA available - using CPU with int8 quantization and base model")

            # Apply auto-detected values only where needed
            if device == "auto":
                device = detected_device
            if compute_type == "auto":
                compute_type = detected_compute
            if model == "auto":
                model = detected_model

        # Validate that the compute type is actually supported on this hardware
        # This prevents crashes on CPUs without AVX2 when int8 is selected
        compute_type = self._select_best_compute_type(device, compute_type)

        return device, compute_type, model

    def _load_model(self):
        """Load the faster-whisper model from the local cache only.

        Cache-first policy: cached models always load with
        ``local_files_only=True`` and never trigger Hugging Face revision or
        metadata checks. A model missing from the cache is NOT downloaded
        here — the backend stays unavailable with ``is_model_missing`` set,
        and the download must be approved through the consent flow (see
        ``download_and_load``).
        """
        try:
            # Importing faster-whisper pulls in PyAV and CTranslate2. Keep that
            # cost off the critical path so the main window can open first.
            model_class = _get_whisper_model_class()

            self._device, self._compute_type, detected_model = self._detect_hardware()

            # Use detected model if current model is "auto"
            if self.model_name == "auto":
                self.model_name = detected_model

            from services.hf_access import is_model_cached

            if not is_model_cached(self.model_name):
                logger.info(
                    f"Model '{self.model_name}' is not in the local cache; "
                    "waiting for download consent"
                )
                self._model_missing = True
                self.model = None
                return

            self._model_missing = False
            logger.info(
                f"Loading faster-whisper model from local cache: {self.model_name} "
                f"(device={self._device}, compute_type={self._compute_type})"
            )

            self.model = model_class(
                self.model_name,
                device=self._device,
                compute_type=self._compute_type,
                local_files_only=True,
            )

            self._last_loaded_model = self.model_name
            logger.info("Faster-whisper model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load faster-whisper model: {e}")
            self.model = None

    def download_and_load(self) -> None:
        """Download the model from Hugging Face, then load it from the cache.

        Only call this after the access policy or an explicit user consent
        permitted the download. Runs synchronously — keep it off the Qt
        thread. A failed download leaves the backend unavailable; it is never
        treated as cached and never falls back to another model.

        Raises:
            Exception: If the download or the subsequent local load fails.
        """
        from services.hf_access import download_model_files

        download_model_files(self.model_name)
        self._load_model()
        if not self.is_available():
            raise Exception(
                f"Model '{self.model_name}' failed to load after download"
            )

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file using faster-whisper model.

        Args:
            audio_path: Path to the audio file to transcribe.

        Returns:
            Transcribed text.

        Raises:
            Exception: If transcription fails or model is not available.
        """
        if not self.is_available():
            raise Exception("Faster-whisper model is not available.")

        try:
            self.is_transcribing = True
            self.reset_cancel_flag()

            logger.info(f"Processing audio with faster-whisper (VAD={config.FASTER_WHISPER_VAD_ENABLED})...")

            # Configure VAD parameters if enabled
            vad_params = None
            if config.FASTER_WHISPER_VAD_ENABLED:
                vad_params = dict(
                    min_silence_duration_ms=config.FASTER_WHISPER_VAD_MIN_SILENCE_MS
                )

            transcript = self._run_with_cuda_fallback(
                lambda: self._transcribe_file_once(audio_path, vad_params),
                operation_name="transcription",
            )

            logger.info(f"Transcription complete. Length: {len(transcript)} characters")

            return transcript

        except Exception as e:
            if "canceled" not in str(e).lower():
                logger.error(f"Transcription failed: {e}")
            raise
        finally:
            self.is_transcribing = False

    def _transcribe_file_once(self, audio_path: str, vad_params: Optional[dict]) -> str:
        """Run one complete model pass and consume its lazy segment generator."""
        from services.settings import resolve_transcription_language

        segments, info = self.model.transcribe(
            audio_path,
            beam_size=config.FASTER_WHISPER_BEAM_SIZE,
            vad_filter=config.FASTER_WHISPER_VAD_ENABLED,
            vad_parameters=vad_params,
            language=resolve_transcription_language(),
        )

        logger.info(
            "Detected language: %s (probability: %.2f)",
            info.language,
            info.language_probability,
        )

        # CTranslate2 does most inference while this generator is consumed, so
        # CUDA errors must be caught around the iteration as well as transcribe().
        text_parts = []
        for segment in segments:
            if self.should_cancel:
                logger.info("Transcription canceled by user")
                raise Exception("Transcription canceled")
            text_parts.append(segment.text)

        import re

        return re.sub(r"\s+", " ", " ".join(text_parts).strip())

    def _is_cuda_runtime_error(self, error: Exception) -> bool:
        """Return whether a GPU failure can be retried safely on CPU."""
        if self._device != "cuda":
            return False

        message = str(error).lower()
        return any(
            marker in message
            for marker in ("cuda", "cublas", "cudnn", "cudart", "nvrtc")
        )

    def _switch_to_cpu_fallback(self) -> None:
        """Replace the failed CUDA model with a CPU/int8 model for this session."""
        failed_device = self._device
        failed_compute_type = self._compute_type

        self.cleanup()
        self._device = "cpu"
        self._compute_type = self._select_best_compute_type("cpu", "int8")

        logger.warning(
            "Reloading model '%s' on CPU after %s/%s failure",
            self.model_name,
            failed_device,
            failed_compute_type,
        )
        self.model = _get_whisper_model_class()(
            self.model_name,
            device=self._device,
            compute_type=self._compute_type,
            local_files_only=True,
        )
        self._last_loaded_model = self.model_name
        self._model_missing = False
        self.reset_cancel_flag()

    def _run_with_cuda_fallback(
        self,
        operation: Callable[[], str],
        operation_name: str,
    ) -> str:
        """Retry a failed CUDA operation once on CPU instead of losing the text."""
        try:
            return operation()
        except Exception as error:
            if not self._is_cuda_runtime_error(error):
                raise

            logger.warning(
                "CUDA %s failed (%s). Retrying once on CPU.",
                operation_name,
                error,
            )
            self._switch_to_cpu_fallback()
            return operation()

    def transcribe_chunks(self, chunk_files: List[str]) -> str:
        """Transcribe multiple audio chunk files efficiently with faster-whisper.

        Args:
            chunk_files: List of paths to audio chunk files.

        Returns:
            Combined transcribed text from all chunks.

        Raises:
            Exception: If transcription fails or model is not available.
        """
        if not self.is_available():
            raise Exception("Faster-whisper model is not available.")

        try:
            self.is_transcribing = True
            self.reset_cancel_flag()

            # Configure VAD parameters if enabled
            vad_params = None
            if config.FASTER_WHISPER_VAD_ENABLED:
                vad_params = dict(
                    min_silence_duration_ms=config.FASTER_WHISPER_VAD_MIN_SILENCE_MS
                )

            combined_text = self._run_with_cuda_fallback(
                lambda: self._transcribe_chunks_once(chunk_files, vad_params),
                operation_name="chunked transcription",
            )

            logger.info(f"Chunked transcription complete. "
                        f"Total length: {len(combined_text)} characters")

            return combined_text

        except Exception as e:
            if "canceled" not in str(e).lower():
                logger.error(f"Chunked transcription failed: {e}")
            raise
        finally:
            self.is_transcribing = False

    def _transcribe_chunks_once(
        self,
        chunk_files: List[str],
        vad_params: Optional[dict],
    ) -> str:
        """Transcribe all chunks once, allowing a clean full retry after CUDA failure."""
        transcriptions = []

        for i, chunk_file in enumerate(chunk_files):
            if self.should_cancel:
                logger.info("Chunked transcription canceled by user")
                raise Exception("Transcription canceled")

            logger.info(f"Processing chunk {i+1}/{len(chunk_files)}: {chunk_file}")
            chunk_text = self._transcribe_file_once(chunk_file, vad_params)
            transcriptions.append(chunk_text)

            logger.info(
                "Chunk %s/%s completed. Length: %s characters",
                i + 1,
                len(chunk_files),
                len(chunk_text),
            )

        from services.audio_processor import audio_processor

        return audio_processor.combine_transcriptions(transcriptions)

    def is_available(self) -> bool:
        """Check if the faster-whisper model is available.

        Returns:
            True if model is loaded and available, False otherwise.
        """
        return self.model is not None

    def reload_model(self, model_name: str = None):
        """Reload the Whisper model with a different model name.

        Args:
            model_name: New model name to load. Reads from settings if None.
        """
        if model_name:
            self.model_name = model_name
        else:
            # Read from settings when no explicit model provided
            from services.settings import SettingsKey, settings_manager
            settings = settings_manager.load_all_settings()
            self.model_name = settings.get(SettingsKey.WHISPER_MODEL, config.DEFAULT_WHISPER_MODEL)
        # Clean up existing model first
        self.cleanup()
        self._load_model()

    def cleanup(self):
        """Clean up faster-whisper model and release resources.

        This unloads the model from memory (including GPU memory if applicable).
        """
        import time

        try:
            if self.model is not None:
                logger.debug("[cleanup] Starting cleanup...")

                # Cancel any ongoing transcription
                self.should_cancel = True

                # Force CUDA to finish ALL pending operations before destroying model
                # This is critical for large models like turbo
                logger.debug("[cleanup] Synchronizing CUDA...")
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.synchronize()
                        logger.debug("[cleanup] CUDA synchronized")
                except ImportError:
                    pass
                except Exception as e:
                    logger.debug(f"[cleanup] CUDA sync error: {e}")

                # Small delay after sync
                time.sleep(0.3)

                logger.debug("[cleanup] Setting model = None...")
                self.model = None
                logger.debug("[cleanup] Model set to None")

                # Give CUDA/ctranslate2 time to finish destructor work
                logger.debug("[cleanup] Sleeping 0.5s...")
                time.sleep(0.5)
                logger.debug("[cleanup] Sleep done")

                # Force garbage collection to release memory
                logger.debug("[cleanup] Calling gc.collect()...")
                import gc
                gc.collect()
                logger.debug("[cleanup] gc.collect() done")

                # Another delay before touching CUDA cache
                time.sleep(0.2)

                # Clear GPU cache
                logger.debug("[cleanup] Clearing CUDA cache...")
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        logger.debug("[cleanup] CUDA cache cleared")
                except ImportError:
                    pass
                except Exception as e:
                    logger.debug(f"[cleanup] CUDA error: {e}")

                logger.debug("[cleanup] Cleanup complete!")
        except Exception as e:
            logger.debug(f"[cleanup] Exception: {e}")

    @property
    def last_loaded_model(self) -> Optional[str]:
        """Resolved name of the most recently loaded model, or None.

        Survives a failed reload to a missing model, so callers can revert the
        selection to a model that is actually present in the cache (e.g. after
        the user cancels a download).
        """
        return self._last_loaded_model

    @property
    def is_model_missing(self) -> bool:
        """True when the requested model is absent from the local cache.

        Distinguishes "needs a consented download" from other load failures
        (e.g. unsupported hardware), so callers know whether the consent flow
        can make the backend available.
        """
        return self._model_missing

    @property
    def name(self) -> str:
        """Get the backend name with model info."""
        device_info = f"{self._device}/{self._compute_type}" if self._device else "not loaded"
        status = "Ready" if self.is_available() else "Not Available"
        return f"FasterWhisper ({self.model_name}, {device_info}) - {status}"

    @property
    def device_info(self) -> str:
        """Get current device, compute type, and model info."""
        if self._model_missing:
            return f"{self.model_name} | not downloaded"
        if self._device and self._compute_type:
            return f"{self.model_name} | {self._device} ({self._compute_type})"
        return "Not initialized"

    @property
    def requires_file_splitting(self) -> bool:
        """faster-whisper can handle files of any size without splitting.

        The library processes audio in a streaming fashion and can handle
        arbitrarily long audio files without memory issues.
        """
        return False
