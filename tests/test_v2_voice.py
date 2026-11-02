import threading
import time

import numpy as np
import pytest

from djorge_local import voice as vm
from djorge_local.voice import Voice, VoiceError, make_wav, parse_wav, resample, pick_voice


class FakeTTS:
    name = "fake"

    def __init__(self):
        self.said, self.voice, self.rate = [], None, None
        self.started = threading.Event()

    def voices(self):
        return ["Microsoft David - English", "Microsoft Maria - Portuguese (Brazil)"]

    def set_voice(self, n):
        self.voice = n

    def set_rate(self, r):
        self.rate = r

    def say(self, text, cancel):
        self.said.append(text)
        self.started.set()
        if text == "longo":
            while not cancel.is_set():
                time.sleep(0.01)

    def halt(self):
        pass


class Seg:
    def __init__(self, t):
        self.text = t


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        return iter([Seg(" olá "), Seg("mundo")]), None


def mk(**kw):
    model = FakeModel()
    tts = FakeTTS()
    v = Voice({"voice": {"model_dir": kw.pop("model_dir", "/nonexistent/x")}},
              tts_factory=kw.pop("tts_factory", lambda: tts),
              whisper_factory=lambda name, d, dl: model,
              whisper_check=kw.pop("whisper_check", lambda: (True, "")))
    return v, tts, model


def test_parse_wav_and_stereo():
    s = np.sin(np.linspace(0, 20, 8000)).astype(np.float32) * 0.5
    out, rate = parse_wav(make_wav(s, 8000))
    assert rate == 8000 and len(out) == 8000
    assert np.allclose(out, s, atol=1e-3)
    with pytest.raises(VoiceError):
        parse_wav(b"nada disso")


def test_resample():
    s = np.zeros(44100, dtype=np.float32)
    assert len(resample(s, 44100)) == 16000
    assert len(resample(np.zeros(16000), 16000)) == 16000


def test_duration_limit():
    v, _, _ = mk()
    with pytest.raises(VoiceError, match="limite"):
        v.transcribe(make_wav(np.zeros(16000 * 61, dtype=np.float32)))


def test_transcribe_fake_model():
    v, _, model = mk()
    r = v.transcribe(make_wav(np.zeros(48000, dtype=np.float32), 48000))
    assert r["text"] == "olá mundo" and isinstance(r["ms"], int)
    audio, kw = model.calls[0]
    assert len(audio) == 16000 and kw["language"] == "pt" and kw["vad_filter"] is True


def test_status_without_engines():
    def boom():
        raise RuntimeError("sem fala")
    v, _, _ = mk(tts_factory=boom, whisper_check=lambda: (False, "faster-whisper não está instalado."))
    st = v.status()
    assert st["tts"] is False and st["stt"] is False
    assert "instalado" in st["stt_error"]
    assert v.speak("oi")["ok"] is False
    with pytest.raises(VoiceError):
        v.transcribe(make_wav(np.zeros(100)))


def test_speak_prefers_ptbr_and_stop():
    v, tts, _ = mk()
    assert v.speak("olá", rate=20)["ok"]
    assert v.wait_idle()
    assert tts.said == ["olá"] and "Maria" in tts.voice and tts.rate == 10
    v.speak("longo")
    assert tts.started.wait(2)
    time.sleep(0.05)
    v.stop()
    assert v.wait_idle(2)


def test_ensure_stt_and_pick_voice():
    v, _, _ = mk()
    st = v.ensure_stt(wait=True)
    assert v.status()["stt"] is True and v.status()["stt_setup"]["state"] == "ready", st
    assert pick_voice(["X", "Microsoft Francisca"]) == "Microsoft Francisca"
    assert pick_voice([]) is None


def test_recorder_js_present():
    assert "/api/voice/transcribe" in vm.RECORDER_JS and "16000" in vm.RECORDER_JS
