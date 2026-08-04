"""Local screen and computer-audio capture for meeting recordings."""

from __future__ import annotations

import logging
import multiprocessing
import os
import shutil
import time
import wave
from pathlib import Path

logger = logging.getLogger(__name__)


def _process_is_alive(process_id: int) -> bool:
    """Return whether a process still exists without depending on psutil."""
    if process_id <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            process_id,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
    except OSError:
        return False
    return True


def _lower_current_process_priority() -> None:
    """Keep capture work responsive without competing with the desktop UI."""
    if os.name != "nt":
        return
    try:
        import ctypes

        below_normal_priority_class = 0x00004000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetPriorityClass(
            kernel32.GetCurrentProcess(),
            below_normal_priority_class,
        )
    except Exception:
        logger.debug("Could not lower capture-process priority", exc_info=True)


def _capture_video_process(
    output_path: str,
    monitor_index: int,
    fps: int,
    crf: int,
    parent_process_id: int,
    stop_event,
    ready_event,
    error_queue,
) -> None:
    """Capture and encode the desktop outside the Qt/Python UI process."""
    container = None
    parent_lost = False
    try:
        _lower_current_process_priority()

        import av
        import mss
        import numpy as np

        with mss.mss() as capture:
            if not 0 < monitor_index < len(capture.monitors):
                raise ValueError("Выбранный монитор не найден")
            monitor = capture.monitors[monitor_index]
            container = av.open(output_path, mode="w")
            stream = container.add_stream("libx264", rate=fps)
            stream.width, stream.height = monitor["width"], monitor["height"]
            stream.pix_fmt = "yuv420p"
            # Screen recordings value responsiveness over motion compression.
            # ``ultrafast`` cuts x264 CPU use substantially; two worker threads
            # keep the encoder from consuming every core on high-DPI displays.
            stream.options = {
                "preset": "ultrafast",
                "tune": "zerolatency",
                "crf": str(crf),
                "threads": "2",
            }
            ready_event.set()

            frame_period = 1 / fps
            next_frame_at = time.monotonic()
            while not stop_event.is_set():
                if not _process_is_alive(parent_process_id):
                    parent_lost = True
                    break
                started = time.monotonic()
                shot = capture.grab(monitor)
                frame = av.VideoFrame.from_ndarray(
                    np.asarray(shot, dtype=np.uint8),
                    format="bgra",
                )
                for packet in stream.encode(frame):
                    container.mux(packet)

                # Schedule against an absolute deadline so a slow encode does
                # not create a busy loop and steal CPU from the foreground app.
                next_frame_at = max(next_frame_at + frame_period, started)
                stop_event.wait(max(0.0, next_frame_at - time.monotonic()))

            for packet in stream.encode():
                container.mux(packet)
    except Exception as exc:
        try:
            error_queue.put_nowait(str(exc))
        except Exception:
            pass
    finally:
        ready_event.set()
        if container is not None:
            try:
                container.close()
            except Exception as exc:
                try:
                    error_queue.put_nowait(str(exc))
                except Exception:
                    pass
        if parent_lost:
            try:
                Path(output_path).unlink(missing_ok=True)
            except OSError:
                pass


def _capture_system_audio_process(
    output_path: str,
    sample_rate: int,
    parent_process_id: int,
    stop_event,
    ready_event,
    error_queue,
) -> None:
    """Capture loopback audio out of process so a driver hang cannot freeze Qt."""
    parent_lost = False
    try:
        _lower_current_process_priority()
        import numpy as np
        import soundcard as sc

        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("Устройство вывода звука не найдено")
        loopback = sc.get_microphone(
            speaker.id,
            include_loopback=True,
        )
        if loopback is None:
            raise RuntimeError("Loopback-устройство звука не найдено")

        path = Path(output_path)
        path.unlink(missing_ok=True)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            with loopback.recorder(
                samplerate=sample_rate,
                channels=2,
                blocksize=2048,
            ) as recorder:
                ready_event.set()
                while not stop_event.is_set():
                    if not _process_is_alive(parent_process_id):
                        parent_lost = True
                        break
                    samples = recorder.record(numframes=2048)
                    pcm = (
                        np.clip(samples, -1.0, 1.0) * 32767.0
                    ).astype(np.int16)
                    output.writeframes(pcm.tobytes())
    except Exception as exc:
        try:
            error_queue.put_nowait(str(exc))
        except Exception:
            pass
    finally:
        ready_event.set()
        if parent_lost:
            # This is an auxiliary file with no owner left to mix or remove it.
            # Do not keep recording after an application crash/forced kill.
            try:
                Path(output_path).unlink(missing_ok=True)
            except OSError:
                pass


class ScreenRecorder:
    """Record one monitor and Windows loopback audio without cloud services."""

    def __init__(
        self,
        output_file: str | Path,
        monitor_index: int = 1,
        fps: int = 15,
        crf: int = 24,
        audio_sample_rate: int = 44100,
        capture_system_audio: bool = True,
    ):
        self.output_file = Path(output_file)
        self.monitor_index = monitor_index
        self.fps = fps
        self.crf = crf
        self.audio_sample_rate = audio_sample_rate
        self.capture_system_audio = capture_system_audio
        self.system_audio_file = self.output_file.with_suffix(".system.wav")

        self.is_recording = False
        self._video_process = None
        self._video_stop_event = None
        self._video_ready_event = None
        self._video_error_queue = None
        self._system_audio_process = None
        self._system_audio_stop_event = None
        self._system_audio_ready_event = None
        self._system_audio_error_queue = None
        self.error: Optional[str] = None
        self.system_audio_error: Optional[str] = None

    def start(self, timeout: float = 4.0) -> bool:
        """Start capture and wait until the MP4 encoder is actually ready."""
        if self.is_recording:
            return False

        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self.error = None
        self.system_audio_error = None
        self.is_recording = True

        context = multiprocessing.get_context("spawn")
        self._video_stop_event = context.Event()
        self._video_ready_event = context.Event()
        self._video_error_queue = context.Queue()
        self._video_process = context.Process(
            target=_capture_video_process,
            args=(
                str(self.output_file),
                self.monitor_index,
                self.fps,
                self.crf,
                os.getpid(),
                self._video_stop_event,
                self._video_ready_event,
                self._video_error_queue,
            ),
            name="meeting-screen-capture",
            daemon=True,
        )
        self._video_process.start()
        video_ready = self._video_ready_event.wait(timeout)
        self._collect_video_error()
        if not video_ready or self.error:
            self._stop_video_process(1.0)
            self.is_recording = False
            if not self.error:
                self.error = "Не удалось запустить кодировщик видео"
            return False

        if self.capture_system_audio:
            self._system_audio_stop_event = context.Event()
            self._system_audio_ready_event = context.Event()
            self._system_audio_error_queue = context.Queue()
            self._system_audio_process = context.Process(
                target=_capture_system_audio_process,
                args=(
                    str(self.system_audio_file),
                    self.audio_sample_rate,
                    os.getpid(),
                    self._system_audio_stop_event,
                    self._system_audio_ready_event,
                    self._system_audio_error_queue,
                ),
                name="meeting-system-audio",
                daemon=True,
            )
            self._system_audio_process.start()
            # Computer audio is an enhancement: a missing loopback device must
            # never prevent microphone + screen capture from continuing.
            self._system_audio_ready_event.wait(min(timeout, 1.0))

        return True

    def stop(self, timeout: float = 12.0) -> bool:
        """Stop capture and wait for MP4/WAV containers to be finalized."""
        if not self.is_recording and not (
            self._video_process and self._video_process.is_alive()
        ):
            return False

        deadline = time.monotonic() + timeout
        self._stop_video_process(max(0.0, deadline - time.monotonic()))
        if self._system_audio_process is not None:
            if self._system_audio_stop_event is not None:
                self._system_audio_stop_event.set()
            self._system_audio_process.join(
                max(0.0, deadline - time.monotonic())
            )
            if self._system_audio_process.is_alive():
                self._system_audio_process.terminate()
                self._system_audio_process.join(1.0)
                self.system_audio_error = (
                    "Захват звука компьютера не завершился"
                )
            if self._system_audio_error_queue is not None:
                try:
                    error = self._system_audio_error_queue.get_nowait()
                except Exception:
                    error = ""
                if error:
                    self.system_audio_error = error
        self.is_recording = False
        return self.error is None

    def _collect_video_error(self) -> None:
        if self._video_error_queue is None:
            return
        try:
            error = self._video_error_queue.get_nowait()
        except Exception:
            error = ""
        if error:
            self.error = error

    def _stop_video_process(self, timeout: float) -> None:
        process = self._video_process
        if process is None:
            return
        if self._video_stop_event is not None:
            self._video_stop_event.set()
        process.join(max(0.0, timeout))
        if process.is_alive():
            process.terminate()
            process.join(1.0)
            self.error = "Кодировщик видео не завершился вовремя"
        self._collect_video_error()

    @staticmethod
    def _reshape_pcm(data: bytes, channels: int, target_channels: int):
        import numpy as np

        if not data:
            return np.empty((0, target_channels), dtype=np.int16)
        samples = np.frombuffer(data, dtype=np.int16).reshape(-1, channels)
        if channels == target_channels:
            return samples
        if channels == 1 and target_channels == 2:
            return np.repeat(samples, 2, axis=1)
        if target_channels == 1:
            return samples.mean(axis=1, keepdims=True).astype(np.int16)
        return samples[:, :target_channels]

    def build_meeting_audio(
        self,
        microphone_wav: str | Path,
        output_wav: str | Path,
    ) -> str:
        """Mix microphone and computer audio into one streaming-safe WAV."""
        microphone_wav = Path(microphone_wav)
        output_wav = Path(output_wav)
        output_wav.parent.mkdir(parents=True, exist_ok=True)

        if (
            self.system_audio_error
            or not self.system_audio_file.exists()
            or self.system_audio_file.stat().st_size <= 44
        ):
            shutil.copy2(microphone_wav, output_wav)
            return str(output_wav)

        import numpy as np

        temporary = output_wav.with_suffix(".mixing.wav")
        try:
            with wave.open(str(microphone_wav), "rb") as microphone, wave.open(
                str(self.system_audio_file), "rb"
            ) as computer:
                if microphone.getsampwidth() != 2 or computer.getsampwidth() != 2:
                    raise ValueError("Поддерживается только 16-битный PCM")
                if microphone.getframerate() != computer.getframerate():
                    raise ValueError("Частота микрофона и звука компьютера различается")

                channels = max(
                    microphone.getnchannels(),
                    computer.getnchannels(),
                )
                with wave.open(str(temporary), "wb") as mixed:
                    mixed.setnchannels(channels)
                    mixed.setsampwidth(2)
                    mixed.setframerate(microphone.getframerate())
                    while True:
                        mic_data = microphone.readframes(8192)
                        pc_data = computer.readframes(8192)
                        if not mic_data and not pc_data:
                            break
                        mic = self._reshape_pcm(
                            mic_data,
                            microphone.getnchannels(),
                            channels,
                        )
                        pc = self._reshape_pcm(
                            pc_data,
                            computer.getnchannels(),
                            channels,
                        )
                        frame_count = max(len(mic), len(pc))
                        if len(mic) < frame_count:
                            mic = np.pad(
                                mic,
                                ((0, frame_count - len(mic)), (0, 0)),
                            )
                        if len(pc) < frame_count:
                            pc = np.pad(
                                pc,
                                ((0, frame_count - len(pc)), (0, 0)),
                            )
                        combined = np.clip(
                            mic.astype(np.float32) * 0.85
                            + pc.astype(np.float32) * 0.85,
                            -32768,
                            32767,
                        ).astype(np.int16)
                        mixed.writeframes(combined.tobytes())
            os.replace(temporary, output_wav)
        except Exception:
            temporary.unlink(missing_ok=True)
            logger.exception("Failed to mix meeting audio; using microphone")
            shutil.copy2(microphone_wav, output_wav)
        return str(output_wav)

    def mux_audio(self, audio_path: str | Path) -> bool:
        """Attach mixed meeting audio to the captured MP4."""
        if not self.output_file.exists():
            return False
        temporary = self.output_file.with_suffix(".muxing.mp4")
        try:
            import av

            with av.open(str(self.output_file)) as video_input, av.open(
                str(audio_path)
            ) as audio_input, av.open(str(temporary), mode="w") as output:
                video_source = video_input.streams.video[0]
                audio_source = audio_input.streams.audio[0]
                video_target = output.add_stream_from_template(video_source)
                audio_target = output.add_stream(
                    "aac",
                    rate=audio_source.rate or self.audio_sample_rate,
                )
                target_layout = (
                    "mono"
                    if audio_source.codec_context.channels == 1
                    else "stereo"
                )
                audio_target.layout = target_layout
                audio_target.bit_rate = 128_000
                resampler = av.AudioResampler(
                    format="fltp",
                    layout=target_layout,
                    rate=audio_target.rate,
                )

                for packet in video_input.demux(video_source):
                    if packet.dts is None:
                        continue
                    packet.stream = video_target
                    output.mux(packet)
                for frame in audio_input.decode(audio_source):
                    for converted in resampler.resample(frame):
                        for packet in audio_target.encode(converted):
                            output.mux(packet)
                for converted in resampler.resample(None):
                    for packet in audio_target.encode(converted):
                        output.mux(packet)
                for packet in audio_target.encode():
                    output.mux(packet)
            os.replace(temporary, self.output_file)
            return True
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            logger.error("Failed to add audio to meeting video: %s", exc)
            return False

    def cleanup_auxiliary(self) -> None:
        """Remove the temporary loopback WAV after muxing or cancellation."""
        if (
            self._system_audio_process is not None
            and self._system_audio_process.is_alive()
        ):
            if self._system_audio_stop_event is not None:
                self._system_audio_stop_event.set()
            self._system_audio_process.join(1.0)
            if self._system_audio_process.is_alive():
                self._system_audio_process.terminate()
                self._system_audio_process.join(1.0)
        self.system_audio_file.unlink(missing_ok=True)
