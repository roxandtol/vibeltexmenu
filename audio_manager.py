"""
audio_manager.py — ASIO-aware audio playback via sounddevice + soundfile.

Supports:
  - Looping BGM playback through a specific ASIO device / channel pair.
  - Crossfading between tracks.
  - One-shot sound effects layered on top of BGM.

ASIO is enabled by setting SD_ENABLE_ASIO=1 before importing sounddevice.
"""

import os
import threading

# Enable ASIO support BEFORE importing sounddevice
os.environ["SD_ENABLE_ASIO"] = "1"

import numpy as np

try:
    import sounddevice as sd
except Exception as e:
    sd = None
    print(f"[audio] WARNING: sounddevice import failed: {e}")

try:
    import soundfile as sf
except Exception as e:
    sf = None
    print(f"[audio] WARNING: soundfile import failed: {e}")


class AudioManager:
    """Manages BGM and SFX playback over an ASIO (or default) output device."""

    def __init__(self, settings: dict):
        self._lock = threading.Lock()
        audio_cfg = settings.get("audio", {})
        self._device = audio_cfg.get("device", "") or None
        self._channels_sel = audio_cfg.get("output_channels", [0, 1])
        self._n_channels = len(self._channels_sel)
        self._sr = audio_cfg.get("sample_rate", 44100)
        self._volume = audio_cfg.get("volume", 0.7)
        self._crossfade_ms = audio_cfg.get("crossfade_ms", 800)

        # Current BGM state
        self._bgm_data: np.ndarray | None = None
        self._bgm_pos = 0
        self._bgm_gain = 1.0
        self._bgm_loop = True

        # Crossfade target
        self._next_data: np.ndarray | None = None
        self._next_pos = 0
        self._fade_samples = 0
        self._fade_progress = 0

        # One-shot SFX
        self._sfx_data: np.ndarray | None = None
        self._sfx_pos = 0

        # Pre-load cache (path → numpy array) to avoid re-decoding on crossfade
        self._cache: dict[str, np.ndarray] = {}

        # Build stream
        self._stream: sd.OutputStream | None = None
        if sd is None or sf is None:
            print("[audio] sounddevice/soundfile not available – audio disabled.")
            return

        # Apply ASIO channel selectors only when the device is an ASIO device
        extra = None
        is_asio = self._device and "asio" in self._device.lower()
        if is_asio:
            try:
                extra = sd.AsioSettings(channel_selectors=self._channels_sel)
                print(f"[audio] Using ASIO channel selectors: {self._channels_sel}")
            except Exception as e:
                print(f"[audio] ASIO settings failed ({e}); falling back to default device.")
                self._device = None

        try:
            self._stream = sd.OutputStream(
                device=self._device,
                samplerate=self._sr,
                channels=self._n_channels,
                dtype="float32",
                callback=self._callback,
                extra_settings=extra,
                blocksize=2048,
            )
            self._stream.start()
            print(f"[audio] Stream opened: device={self._device!r}, "
                  f"ch={self._channels_sel}, sr={self._sr}")
        except Exception as e:
            print(f"[audio] ERROR opening stream: {e}")
            self._stream = None

    # ── internal ────────────────────────────────────────────────────────────

    def _load_file(self, path: str) -> np.ndarray | None:
        """Decode an audio file to float32 numpy, resampled/re-channelled.
        Results are cached to avoid repeated disk I/O."""
        if not path or not os.path.isfile(path):
            return None

        # Check cache first
        if path in self._cache:
            return self._cache[path]

        try:
            data, file_sr = sf.read(path, dtype="float32", always_2d=True)
        except Exception as e:
            print(f"[audio] Failed to decode {path}: {e}")
            return None

        # Resample if needed (simple nearest-neighbour, fine for BGM)
        if file_sr != self._sr:
            ratio = self._sr / file_sr
            indices = np.round(np.arange(0, len(data), 1 / ratio)).astype(int)
            indices = indices[indices < len(data)]
            data = data[indices]

        # Channel adaptation
        if data.shape[1] < self._n_channels:
            data = np.tile(data[:, :1], (1, self._n_channels))
        elif data.shape[1] > self._n_channels:
            data = data[:, :self._n_channels]

        # Ensure contiguous for fast slicing in callback
        data = np.ascontiguousarray(data)
        self._cache[path] = data
        return data

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status):
        """PortAudio callback — mix BGM + crossfade + SFX.
        This runs in a high-priority audio thread; keep it fast."""
        with self._lock:
            out = np.zeros((frames, self._n_channels), dtype="float32")

            # ── BGM ──────────────────────────────────────────────────────
            if self._bgm_data is not None:
                bgm_len = len(self._bgm_data)
                end = self._bgm_pos + frames
                if end <= bgm_len:
                    out += self._bgm_data[self._bgm_pos:end] * self._bgm_gain
                    self._bgm_pos = end
                else:
                    remaining = bgm_len - self._bgm_pos
                    if remaining > 0:
                        out[:remaining] += self._bgm_data[self._bgm_pos:] * self._bgm_gain
                    if self._bgm_loop and bgm_len > 0:
                        leftover = frames - remaining
                        self._bgm_pos = leftover % bgm_len
                        # Fill from the start (simple single-wrap for typical block sizes)
                        if leftover <= bgm_len:
                            out[remaining:remaining + leftover] += self._bgm_data[:leftover] * self._bgm_gain
                        else:
                            pos = remaining
                            while pos < frames:
                                n = min(bgm_len, frames - pos)
                                out[pos:pos + n] += self._bgm_data[:n] * self._bgm_gain
                                pos += n
                            self._bgm_pos = (frames - remaining) % bgm_len
                    else:
                        self._bgm_pos = bgm_len

            # ── Crossfade target ─────────────────────────────────────────
            if self._next_data is not None and self._fade_samples > 0:
                next_len = len(self._next_data)
                end_n = self._next_pos + frames
                if end_n <= next_len:
                    nchunk = self._next_data[self._next_pos:end_n]
                    self._next_pos = end_n
                else:
                    remaining_n = next_len - self._next_pos
                    nchunk = np.zeros((frames, self._n_channels), dtype="float32")
                    if remaining_n > 0:
                        nchunk[:remaining_n] = self._next_data[self._next_pos:]
                    if self._bgm_loop and next_len > 0:
                        leftover_n = frames - remaining_n
                        self._next_pos = leftover_n % next_len
                        if leftover_n <= next_len:
                            nchunk[remaining_n:remaining_n + leftover_n] = self._next_data[:leftover_n]
                        else:
                            pos_n = remaining_n
                            while pos_n < frames:
                                n = min(next_len, frames - pos_n)
                                nchunk[pos_n:pos_n + n] = self._next_data[:n]
                                pos_n += n
                            self._next_pos = (frames - remaining_n) % next_len
                    else:
                        self._next_pos = next_len

                # Per-frame fade envelope
                fade_start = self._fade_progress
                fade_end = min(fade_start + frames, self._fade_samples)
                env = np.linspace(fade_start / self._fade_samples,
                                  fade_end / self._fade_samples,
                                  frames, dtype="float32").reshape(-1, 1)

                out *= (1.0 - env)
                out += nchunk * env

                self._fade_progress += frames
                if self._fade_progress >= self._fade_samples:
                    self._bgm_data = self._next_data
                    self._bgm_pos = self._next_pos
                    self._bgm_gain = 1.0
                    self._next_data = None
                    self._fade_samples = 0
                    self._fade_progress = 0

            # ── SFX overlay ──────────────────────────────────────────────
            if self._sfx_data is not None:
                sfx_remaining = len(self._sfx_data) - self._sfx_pos
                n = min(frames, sfx_remaining)
                out[:n] += self._sfx_data[self._sfx_pos:self._sfx_pos + n]
                self._sfx_pos += n
                if self._sfx_pos >= len(self._sfx_data):
                    self._sfx_data = None

            outdata[:] = out * self._volume

    # ── public API ──────────────────────────────────────────────────────────

    def play_bgm(self, filepath: str, loop: bool = True):
        """Start playing a BGM track (replaces current immediately)."""
        data = self._load_file(filepath)
        with self._lock:
            self._bgm_data = data
            self._bgm_pos = 0
            self._bgm_gain = 1.0
            self._bgm_loop = loop
            self._next_data = None
            self._fade_samples = 0
            self._fade_progress = 0

    def crossfade_to(self, filepath: str, duration_ms: int | None = None):
        """Crossfade from the current BGM to a new track."""
        if duration_ms is None:
            duration_ms = self._crossfade_ms
        data = self._load_file(filepath)
        if data is None:
            return
        with self._lock:
            self._next_data = data
            self._next_pos = 0
            self._fade_samples = int(self._sr * duration_ms / 1000)
            self._fade_progress = 0

    def play_sfx(self, filepath: str):
        """Play a one-shot sound effect mixed on top of BGM."""
        data = self._load_file(filepath)
        if data is None:
            return
        with self._lock:
            self._sfx_data = data
            self._sfx_pos = 0

    def stop(self):
        """Stop all playback."""
        with self._lock:
            self._bgm_data = None
            self._next_data = None
            self._sfx_data = None

    def shutdown(self):
        """Close the audio stream."""
        self.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        print("[audio] Shut down.")
