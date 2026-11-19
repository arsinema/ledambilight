"""
Захват системного звука (loopback — то, что реально играет из колонок,
а не микрофон) и извлечение параметров для аудио-реактивной подсветки:
громкость (RMS), энергия по полосам (бас/mid/treble), детекция пиков
(взрывы, удары, бас-дропы).

Работает в собственном потоке, т.к. чтение звука через `soundcard` —
блокирующая операция. Наружу отдаёт потокобезопасный снимок состояния
через get_state() — можно дёргать из любого потока без блокировок.
"""

import logging
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

# soundcard инициализирует COM/WASAPI. Импортируем его лениво: иначе порядок
# импорта модулей (audio до capture) ломает dxcam/comtypes в том же потоке.
_sc = None
_sc_loaded = False
_sc_lock = threading.Lock()


def _soundcard():
    global _sc, _sc_loaded
    if _sc_loaded:
        return _sc
    with _sc_lock:
        if _sc_loaded:
            return _sc
        try:
            import soundcard as module
            _sc = module
        except Exception as exc:
            # На системах без WASAPI/аудио-бэкенда не роняем приложение.
            log.warning("soundcard недоступен: %s", exc)
            _sc = None
        _sc_loaded = True
    return _sc

SAMPLE_RATE = 48000  # большинство USB-аудиокарт работают в 48 кГц
CHUNK_SIZE = 1024  # ~23мс на чанк при 44100Hz — хороший баланс отклика/нагрузки
SPECTRUM_BANDS = 32  # столько полос отдаём в режим «спектроанализатор»
SPECTRUM_LOW_HZ = 40
SPECTRUM_HIGH_HZ = 16000


class AudioState:
    __slots__ = (
        "volume", "bass", "mid", "treble",
        "last_peak_time", "last_peak_strength", "spectrum",
        "beat", "bpm",
    )

    def __init__(self):
        self.volume = 0.0
        self.bass = 0.0
        self.mid = 0.0
        self.treble = 0.0
        self.last_peak_time = -1e9
        self.last_peak_strength = 0.0
        self.spectrum = [0.0] * SPECTRUM_BANDS
        self.beat = 0.0     # сила удара в текущем кадре (0..1)
        self.bpm = 0.0

    def copy(self):
        s = AudioState()
        s.volume, s.bass, s.mid, s.treble = self.volume, self.bass, self.mid, self.treble
        s.last_peak_time = self.last_peak_time
        s.last_peak_strength = self.last_peak_strength
        s.spectrum = list(self.spectrum)
        s.beat = self.beat
        s.bpm = self.bpm
        return s


def is_available():
    return _soundcard() is not None


def list_output_devices():
    """Названия устройств вывода звука, доступных для loopback-захвата."""
    module = _soundcard()
    if module is None:
        return []
    try:
        return [spk.name for spk in module.all_speakers()]
    except Exception:
        return []


def list_input_devices():
    """Микрофоны, линейные/инструментальные входы и аудиоинтерфейсы."""
    module = _soundcard()
    if module is None:
        return []
    try:
        return [
            mic.name
            for mic in module.all_microphones(include_loopback=False)
            if "стерео микшер" not in mic.name.lower()
            and "stereo mix" not in mic.name.lower()
        ]
    except Exception:
        return []


class AudioCapture:
    def __init__(self):
        self._thread = None
        self._running = False
        # Номер поколения: старый поток не должен «ожить» после stop()+start(),
        # иначе два потока начнут читать звук одновременно
        self._generation = 0
        self._lock = threading.Lock()
        self._state = AudioState()
        self.last_error = ""

        self.device_name = None  # None = устройство по умолчанию
        self.source = "loopback"  # "loopback" (звук колонок) | "input" (микрофон/гитара)
        self.input_gain = 2.0

        # Параметры анализа — обновляются на лету через update_settings()
        self.sensitivity = 1.0
        self.bass_gain = 1.0
        self.mid_gain = 1.0
        self.treble_gain = 1.0
        self.envelope_attack = 0.5     # 0-1, скорость роста огибающей
        self.envelope_release = 0.15   # 0-1, скорость спада огибающей
        self.peak_threshold = 1.8      # во сколько раз энергия должна превысить среднее

        self._energy_avg = 0.0
        self._last_peak_time = -1e9
        self._peak_min_gap = 0.12      # мин. пауза между пиками, сек

        self._sm_volume = 0.0
        self._sm_bass = 0.0
        self._sm_mid = 0.0
        self._sm_treble = 0.0

        # Предвычисляем то, что не меняется от чанка к чанку - раньше окно
        # Ханнинга и частотная сетка FFT пересчитывались на КАЖДОМ чанке
        # (~43 раза в секунду), хотя они всегда одного размера. Экономит
        # заметную долю CPU при включённом аудио-режиме.
        self._hanning_window = np.hanning(CHUNK_SIZE)
        self._freqs = np.fft.rfftfreq(CHUNK_SIZE, d=1.0 / SAMPLE_RATE)
        self._bass_mask = (self._freqs >= 20) & (self._freqs < 250)
        self._mid_mask = (self._freqs >= 250) & (self._freqs < 4000)
        self._treble_mask = (self._freqs >= 4000) & (self._freqs < 16000)

        # Полосы спектроанализатора: логарифмическая сетка (как в эквалайзерах),
        # чтобы низкие частоты не занимали весь диапазон
        edges = np.geomspace(SPECTRUM_LOW_HZ, SPECTRUM_HIGH_HZ, SPECTRUM_BANDS + 1)
        indices = np.searchsorted(self._freqs, edges)
        self._spectrum_slices = []
        for i in range(SPECTRUM_BANDS):
            start = int(min(indices[i], len(self._freqs) - 1))
            end = int(max(start + 1, min(indices[i + 1], len(self._freqs))))
            self._spectrum_slices.append((start, end))
        # Автоматическая подстройка под громкость: общий пик по всем полосам
        self._spectrum_peak = 1e-4
        self._sm_spectrum = np.zeros(SPECTRUM_BANDS, dtype=np.float64)

        # Детекция ритма: спектральный поток (spectral flux)
        self._prev_spectrum = None
        self._flux_avg = 1e-4
        self._last_beat_time = -1e9
        self._bpm = 0.0
        self.beat_threshold = 1.6
        self._sm_beat = 0.0

    @property
    def is_running(self):
        return self._running

    def update_settings(self, audio_cfg):
        self.sensitivity = audio_cfg.get("sensitivity", 1.0)
        self.bass_gain = audio_cfg.get("bass_gain", 1.0)
        self.mid_gain = audio_cfg.get("mid_gain", 1.0)
        self.treble_gain = audio_cfg.get("treble_gain", 1.0)
        self.envelope_attack = audio_cfg.get("envelope_attack", 0.5)
        self.envelope_release = audio_cfg.get("envelope_release", 0.15)
        self.peak_threshold = audio_cfg.get("peak_threshold", 1.8)
        self.device_name = audio_cfg.get("device_name") or None
        self.source = audio_cfg.get("source", "loopback") or "loopback"
        try:
            self.input_gain = max(0.5, min(8.0, float(audio_cfg.get("input_gain", 2.0))))
        except (TypeError, ValueError):
            self.input_gain = 2.0

    def start(self):
        if _soundcard() is None:
            raise RuntimeError(
                "Библиотека 'soundcard' недоступна. Установите: pip install soundcard"
            )
        if self._running:
            return
        self._generation += 1
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(self._generation,), daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._generation += 1
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def get_state(self):
        with self._lock:
            return self._state.copy()

    # ------------------------------------------------------------------

    def _run(self, generation):
        # soundcard работает через WASAPI, а ему нужен инициализированный COM
        # именно в этом потоке. В главном потоке его инициализирует Qt, поэтому
        # без этого вызова захват падал с CO_E_NOTINITIALIZED (0x800401f0) и
        # «аудио-реакция» молча не включалась.
        com_initialized = False
        try:
            import ctypes
            result = ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
            com_initialized = result in (0, 1)  # S_OK / S_FALSE
        except Exception:
            log.debug("Не удалось инициализировать COM в потоке звука", exc_info=True)

        try:
            module = _soundcard()
            if module is None:
                raise RuntimeError("Библиотека soundcard недоступна")
            if self.source == "input":
                mic = None
                if self.device_name:
                    for candidate in module.all_microphones(include_loopback=False):
                        if candidate.name == self.device_name:
                            mic = candidate
                            break
                if mic is None:
                    mic = module.default_microphone()
                if mic is None:
                    raise RuntimeError(
                        "Не найден микрофон/линейный вход. Проверь, что аудиокарта "
                        "включена в Windows → Звук → Запись."
                    )
                log.info("Аудио-реакция: вход %s", mic.name)
            else:
                speaker = None
                if self.device_name:
                    for spk in module.all_speakers():
                        if spk.name == self.device_name:
                            speaker = spk
                            break
                if speaker is None:
                    speaker = module.default_speaker()
                if speaker is None:
                    raise RuntimeError("Не найдено устройство вывода звука")
                mic = module.get_microphone(id=str(speaker.name), include_loopback=True)
                log.info("Аудио-реакция: loopback %s", speaker.name)

            recorder = None
            for rate in (SAMPLE_RATE, 44100):
                try:
                    recorder = mic.recorder(samplerate=rate)
                    log.info("Аудио-реакция: частота %s Гц", rate)
                    break
                except Exception as exc:
                    log.warning("Не удалось открыть звук на %s Гц: %s", rate, exc)
            if recorder is None:
                raise RuntimeError("Аудиокарта не открылась ни на 48, ни на 44.1 кГц")

            with recorder as active:
                while self._running and generation == self._generation:
                    data = active.record(numframes=CHUNK_SIZE)
                    self._process_chunk(data)
        except Exception as exc:
            # Раньше поток просто тихо умирал, и «аудио-реакция» переставала
            # работать без единого сообщения
            log.exception("Поток захвата звука остановлен из-за ошибки")
            if generation == self._generation:
                self._running = False
                self.last_error = str(exc)
        finally:
            if com_initialized:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def _process_chunk(self, data):
        # data: (frames, channels) float32 в диапазоне -1..1
        mono = data.mean(axis=1) if data.ndim > 1 else data

        rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)) + 1e-9)
        # эмпирический множитель: чистый RMS системного звука редко доходит
        # до 1.0, поэтому подтягиваем шкалу под более отзывчивый диапазон
        input_gain = self.input_gain if self.source == "input" else 1.0
        volume = min(1.0, rms * 4.0 * self.sensitivity * input_gain)

        windowed = mono * self._hanning_window if len(mono) == CHUNK_SIZE else mono * np.hanning(len(mono))
        spectrum = np.abs(np.fft.rfft(windowed))

        if len(mono) == CHUNK_SIZE:
            # Обычный случай - используем предвычисленные частоты и маски полос
            bass = self._band_energy_masked(spectrum, self._bass_mask, self.bass_gain)
            mid = self._band_energy_masked(spectrum, self._mid_mask, self.mid_gain)
            treble = self._band_energy_masked(spectrum, self._treble_mask, self.treble_gain)
            self._update_spectrum(spectrum)
            self._detect_beat(spectrum)
        else:
            # Редкий случай другого размера чанка - считаем как раньше, на лету
            freqs = np.fft.rfftfreq(len(mono), d=1.0 / SAMPLE_RATE)
            bass = self._band_energy(spectrum, freqs, 20, 250, self.bass_gain)
            mid = self._band_energy(spectrum, freqs, 250, 4000, self.mid_gain)
            treble = self._band_energy(spectrum, freqs, 4000, 16000, self.treble_gain)

        if input_gain != 1.0:
            bass = min(1.0, bass * input_gain)
            mid = min(1.0, mid * input_gain)
            treble = min(1.0, treble * input_gain)

        self._sm_volume = self._envelope(self._sm_volume, volume)
        self._sm_bass = self._envelope(self._sm_bass, bass)
        self._sm_mid = self._envelope(self._sm_mid, mid)
        self._sm_treble = self._envelope(self._sm_treble, treble)

        now = time.monotonic()
        if self._energy_avg <= 0:
            self._energy_avg = rms
        else:
            self._energy_avg = self._energy_avg * 0.98 + rms * 0.02

        peak_detected = False
        peak_strength = 0.0
        if (
            self._energy_avg > 1e-6
            and rms > self._energy_avg * self.peak_threshold
            and now - self._last_peak_time > self._peak_min_gap
        ):
            peak_detected = True
            peak_strength = min(1.0, (rms / (self._energy_avg * self.peak_threshold)) - 1.0 + 0.4)
            self._last_peak_time = now

        with self._lock:
            self._state.volume = self._sm_volume
            self._state.bass = self._sm_bass
            self._state.mid = self._sm_mid
            self._state.treble = self._sm_treble
            self._state.spectrum = [float(v) for v in self._sm_spectrum]
            self._state.beat = float(self._sm_beat)
            self._state.bpm = float(self._bpm)
            if peak_detected:
                self._state.last_peak_time = now
                self._state.last_peak_strength = peak_strength

    def _detect_beat(self, spectrum):
        """Определяет удар по спектральному потоку ( spectral flux ).

        Суммируем только положительные приросты спектра: резкий вход баса или
        бочки даёт всплеск, плавное нарастание - нет. Среднее значение потока
        служит порогом, поэтому в тишине ничего не срабатывает.
        """
        if self._prev_spectrum is None or self._prev_spectrum.shape != spectrum.shape:
            self._prev_spectrum = spectrum
            return

        flux = float(np.maximum(0.0, spectrum - self._prev_spectrum).sum())
        self._prev_spectrum = spectrum
        self._flux_avg = self._flux_avg * 0.95 + flux * 0.05

        now = time.monotonic()
        strength = 0.0
        if (self._flux_avg > 1e-6
                and flux > self._flux_avg * self.beat_threshold
                and now - self._last_beat_time > 0.14):
            strength = min(1.0, flux / (self._flux_avg * self.beat_threshold) - 1.0 + 0.35)
            interval = now - self._last_beat_time
            if 0.3 < interval < 1.5:
                bpm = 60.0 / interval
                self._bpm = bpm if self._bpm <= 0 else self._bpm * 0.8 + bpm * 0.2
            self._last_beat_time = now

        # Затухание: вспышка живёт несколько кадров
        self._sm_beat = max(strength, self._sm_beat * 0.75)

    def _update_spectrum(self, spectrum):
        """32 полосы для спектроанализатора.

        Нормируем на ОБЩИЙ максимум по всем полосам (с медленным спадом), а не
        на пик каждой полосы отдельно: иначе тихая, но ровная полоса (шум,
        наводка) показывалась бы как «полная громкость»."""
        energies = np.zeros(SPECTRUM_BANDS, dtype=np.float64)
        for i, (start, end) in enumerate(self._spectrum_slices):
            if end > start:
                energies[i] = float(spectrum[start:end].mean())

        current_peak = float(energies.max()) if energies.size else 0.0
        self._spectrum_peak = max(self._spectrum_peak * 0.99, current_peak, 1e-4)
        normalized = np.clip(energies / self._spectrum_peak, 0.0, 1.0)

        for i in range(SPECTRUM_BANDS):
            self._sm_spectrum[i] = self._envelope(self._sm_spectrum[i], float(normalized[i]))

    @staticmethod
    def _band_energy(spectrum, freqs, low, high, gain):
        mask = (freqs >= low) & (freqs < high)
        if not np.any(mask):
            return 0.0
        e = float(np.mean(spectrum[mask]))
        return min(1.0, e * 0.02 * gain)

    @staticmethod
    def _band_energy_masked(spectrum, mask, gain):
        """То же самое, что _band_energy, но с уже готовой (предвычисленной) маской -
        не пересчитывает mask на каждый вызов."""
        if not np.any(mask):
            return 0.0
        e = float(np.mean(spectrum[mask]))
        return min(1.0, e * 0.02 * gain)

    def _envelope(self, current, target):
        rate = self.envelope_attack if target > current else self.envelope_release
        rate = max(0.01, min(1.0, rate))
        return current + (target - current) * rate
