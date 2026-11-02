"""Voz do Djorge Local: fala (TTS) e reconhecimento de fala (STT), tudo no computador.

- TTS: SAPI do Windows (comtypes ou pywin32), com alternativa pyttsx3. Roda em thread própria
  e pode ser interrompido por ``stop()``. Em outras plataformas ``status()`` informa ``tts=False``.
- STT: faster-whisper local (opcional), carregado sob demanda. ``ensure_stt()`` baixa o modelo
  para ``%LOCALAPPDATA%/Djorge/voz`` só quando a pessoa pede; o progresso aparece em ``status()``.
- Nenhum áudio sai do computador e nada é gravado em disco (o WAV é decodificado em memória).

Configuração opcional em ``cfg["voice"]``: ``{"stt_model": "base"|"small", "voice": "Maria",
"rate": 0, "max_seconds": 60, "model_dir": "..."}``.

Uso pela interface (rotas do agente A):
  GET  /api/voice/status      -> Voice.status()
  POST /api/voice/speak {text, voice?, rate?} -> Voice.speak(...)
  POST /api/voice/stop        -> Voice.stop()
  POST /api/voice/transcribe {audio_b64} -> Voice.transcribe(base64.b64decode(audio_b64))
  POST /api/voice/setup       -> Voice.ensure_stt()
Se ``status()["stt"]`` for False, a interface usa ``webkitSpeechRecognition`` do WebView2, se existir.
O gravador JS pronto para uso está em ``RECORDER_JS``.
"""
from __future__ import annotations

import io
import os
import queue
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np

TARGET_RATE = 16000
MAX_SECONDS = 60
PT_HINTS = ("pt-br", "pt_br", "portugu", "brazil", "brasil", "maria", "francisca", "daniel", "thalita", "antonio")


class VoiceError(ValueError):
    """Erro com mensagem amigável em português."""


# ---------------------------------------------------------------- WAV
def parse_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decodifica WAV PCM 16-bit (mono ou multicanal -> mono). Devolve (float32 em [-1,1], taxa)."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise VoiceError("Áudio inválido: esperado arquivo WAV.")
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            if len(body) < 16:
                raise VoiceError("Áudio inválido: cabeçalho WAV incompleto.")
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            pcm = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        raise VoiceError("Áudio inválido: WAV sem cabeçalho ou sem dados.")
    tag, channels, rate, _, _, bits = fmt
    if tag not in (1, 0xFFFE) or bits != 16:
        raise VoiceError("Áudio não suportado: use WAV PCM de 16 bits.")
    if channels < 1 or rate <= 0:
        raise VoiceError("Áudio inválido: canais ou taxa incorretos.")
    pcm = pcm[: len(pcm) - (len(pcm) % (2 * channels))]
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def resample(samples: np.ndarray, rate: int, target: int = TARGET_RATE) -> np.ndarray:
    """Reamostragem linear simples (suficiente para fala)."""
    if rate == target or len(samples) == 0:
        return samples.astype(np.float32)
    n = max(1, int(round(len(samples) * target / rate)))
    x_old = np.arange(len(samples)) / rate
    x_new = np.arange(n) / target
    return np.interp(x_new, x_old, samples).astype(np.float32)


def make_wav(samples: np.ndarray, rate: int = TARGET_RATE) -> bytes:
    """Gera WAV PCM 16-bit mono (útil para testes)."""
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    buf.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE")
    buf.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
    buf.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
    return buf.getvalue()


def default_model_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Djorge" / "voz"


# ---------------------------------------------------------------- TTS engines
class _SapiEngine:
    """SAPI.SpVoice via comtypes ou pywin32. Criado e usado só na thread de fala."""
    name = "sapi"
    SVSF_ASYNC, SVSF_PURGE = 1, 2

    def __init__(self):
        try:
            import comtypes.client as cc  # type: ignore
            cc.CoInitialize() if hasattr(cc, "CoInitialize") else None
            self.v = cc.CreateObject("SAPI.SpVoice")
        except Exception:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
            pythoncom.CoInitialize()
            self.v = win32com.client.Dispatch("SAPI.SpVoice")
        self._tokens = None

    def voices(self) -> list[str]:
        toks = self.v.GetVoices()
        self._tokens = [toks.Item(i) for i in range(toks.Count)]
        return [t.GetDescription() for t in self._tokens]

    def set_voice(self, name: str):
        if self._tokens is None:
            self.voices()
        for t in self._tokens:
            if t.GetDescription() == name:
                self.v.Voice = t
                return

    def set_rate(self, rate: int):
        self.v.Rate = int(rate)

    def say(self, text: str, cancel: threading.Event):
        self.v.Speak(text, self.SVSF_ASYNC)
        while not self.v.WaitUntilDone(100):
            if cancel.is_set():
                self.v.Speak("", self.SVSF_ASYNC | self.SVSF_PURGE)
                return

    def halt(self):
        pass  # a interrupção acontece no laço de say()


class _Pyttsx3Engine:
    name = "pyttsx3"

    def __init__(self):
        import pyttsx3  # type: ignore
        self.e = pyttsx3.init()
        self._map = {}

    def voices(self) -> list[str]:
        vs = self.e.getProperty("voices") or []
        self._map = {v.name: v.id for v in vs}
        return list(self._map)

    def set_voice(self, name: str):
        if not self._map:
            self.voices()
        if name in self._map:
            self.e.setProperty("voice", self._map[name])

    def set_rate(self, rate: int):
        self.e.setProperty("rate", int(200 + rate * 10))  # -10..10 -> 100..300 ppm

    def say(self, text: str, cancel: threading.Event):
        if cancel.is_set():
            return
        self.e.say(text)
        self.e.runAndWait()

    def halt(self):
        try:
            self.e.stop()
        except Exception:
            pass


def _default_tts_factory():
    if sys.platform != "win32":
        raise RuntimeError("Fala disponível apenas no Windows.")
    errors = []
    for cls in (_SapiEngine, _Pyttsx3Engine):
        try:
            return cls()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cls.name}: {e}")
    raise RuntimeError("Nenhum mecanismo de fala disponível (" + "; ".join(errors) + ").")


def pick_voice(names: list[str]) -> str | None:
    """Prefere vozes em português do Brasil."""
    for hint in PT_HINTS:
        for n in names:
            if hint in n.lower():
                return n
    return names[0] if names else None


def _default_whisper_factory(model: str, model_dir: Path, download: bool):
    from faster_whisper import WhisperModel  # type: ignore
    return WhisperModel(model, device="cpu", compute_type="int8",
                        download_root=str(model_dir), local_files_only=not download)


def _whisper_available() -> tuple[bool, str]:
    try:
        import importlib.util
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper não está instalado."
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"faster-whisper indisponível: {e}"


# ---------------------------------------------------------------- Voice
class Voice:
    def __init__(self, cfg: dict | None = None, *, tts_factory=None, whisper_factory=None,
                 whisper_check=None):
        vc = dict((cfg or {}).get("voice") or {})
        self.model_name = vc.get("stt_model", "base")
        self.model_dir = Path(vc.get("model_dir") or default_model_dir())
        self.max_seconds = float(vc.get("max_seconds", MAX_SECONDS))
        self.default_voice = vc.get("voice")
        self.default_rate = int(vc.get("rate", 0))
        self._tts_factory = tts_factory or _default_tts_factory
        self._whisper_factory = whisper_factory or _default_whisper_factory
        self._whisper_check = whisper_check or _whisper_available
        # TTS
        self._q: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._speaking = threading.Event()
        self._tts_ready = threading.Event()
        self._tts_engine = None
        self._tts_error = ""
        self._voices: list[str] = []
        self._tts_thread = None
        # STT
        self._model = None
        self._lock = threading.Lock()
        self._stt_state = {"state": "idle", "progress": 0.0, "message": ""}
        self._stt_thread = None

    # ---- TTS
    def _ensure_tts_thread(self, timeout: float = 5.0):
        if self._tts_thread is None:
            self._tts_thread = threading.Thread(target=self._tts_loop, name="djorge-voz", daemon=True)
            self._tts_thread.start()
        self._tts_ready.wait(timeout)

    def _tts_loop(self):
        try:
            self._tts_engine = self._tts_factory()
            self._voices = list(self._tts_engine.voices())
        except Exception as e:  # noqa: BLE001
            self._tts_error = str(e)
            self._tts_engine = None
        self._tts_ready.set()
        while True:
            item = self._q.get()
            if item is None:
                return
            text, voice, rate = item
            if self._tts_engine is None or self._cancel.is_set():
                continue
            self._speaking.set()
            try:
                v = voice or self.default_voice or pick_voice(self._voices)
                if v:
                    self._tts_engine.set_voice(v)
                self._tts_engine.set_rate(max(-10, min(10, int(rate if rate is not None else self.default_rate))))
                self._tts_engine.say(text, self._cancel)
            except Exception as e:  # noqa: BLE001
                self._tts_error = f"Falha ao falar: {e}"
            finally:
                self._speaking.clear()

    def speak(self, text: str, voice: str | None = None, rate: int | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Nada para falar."}
        self._ensure_tts_thread()
        if self._tts_engine is None:
            return {"ok": False, "error": self._tts_error or "Fala indisponível."}
        self.stop()
        self._cancel.clear()
        self._q.put((text[:5000], voice, rate))
        return {"ok": True}

    def stop(self) -> dict:
        self._cancel.set()
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        if self._tts_engine is not None:
            self._tts_engine.halt()
        return {"ok": True}

    def wait_idle(self, timeout: float = 5.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self._q.empty() and not self._speaking.is_set():
                return True
            time.sleep(0.01)
        return False

    def list_voices(self) -> list[str]:
        self._ensure_tts_thread()
        return list(self._voices)

    # ---- STT
    def _stt_ok(self) -> tuple[bool, str]:
        return self._whisper_check()

    def _load_model(self, download: bool):
        with self._lock:
            if self._model is None:
                self._model = self._whisper_factory(self.model_name, self.model_dir, download)
            return self._model

    def ensure_stt(self, wait: bool = False) -> dict:
        """Baixa/carrega o modelo de fala em segundo plano. Progresso em status()['stt_setup']."""
        ok, why = self._stt_ok()
        if not ok:
            self._stt_state = {"state": "error", "progress": 0.0, "message": why}
            return dict(self._stt_state)
        if self._model is not None:
            self._stt_state = {"state": "ready", "progress": 1.0, "message": "Reconhecimento de fala pronto."}
            return dict(self._stt_state)
        if self._stt_thread is None or not self._stt_thread.is_alive():
            self._stt_state = {"state": "downloading", "progress": 0.0,
                               "message": f"Baixando modelo de fala '{self.model_name}'..."}
            self._stt_thread = threading.Thread(target=self._setup_worker, daemon=True)
            self._stt_thread.start()
        if wait:
            self._stt_thread.join()
        return dict(self._stt_state)

    def _dir_size(self) -> int:
        try:
            return sum(p.stat().st_size for p in self.model_dir.rglob("*") if p.is_file())
        except OSError:
            return 0

    def _setup_worker(self):
        expected = {"base": 145e6, "small": 485e6}.get(self.model_name, 300e6)
        done = threading.Event()

        def watch():
            while not done.wait(0.5):
                self._stt_state["progress"] = round(min(0.99, self._dir_size() / expected), 3)

        threading.Thread(target=watch, daemon=True).start()
        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            self._load_model(download=True)
            self._stt_state = {"state": "ready", "progress": 1.0, "message": "Reconhecimento de fala pronto."}
        except Exception as e:  # noqa: BLE001
            self._stt_state = {"state": "error", "progress": 0.0, "message": f"Falha ao baixar o modelo: {e}"}
        finally:
            done.set()

    def transcribe(self, wav_bytes: bytes, lang: str = "pt") -> dict:
        ok, why = self._stt_ok()
        if not ok:
            raise VoiceError(why)
        samples, rate = parse_wav(wav_bytes)
        dur = len(samples) / rate
        if dur > self.max_seconds:
            raise VoiceError(f"Áudio longo demais ({dur:.0f} s). O limite é {self.max_seconds:.0f} s.")
        audio = resample(samples, rate)
        t0 = time.perf_counter()
        try:
            model = self._load_model(download=False)
        except Exception as e:  # noqa: BLE001
            raise VoiceError("Modelo de fala não instalado. Use 'Configurar voz' para baixá-lo.") from e
        if len(audio) < TARGET_RATE // 10:
            return {"text": "", "ms": 0}
        segments, _info = model.transcribe(audio, language=lang or "pt", vad_filter=True, beam_size=1)
        text = " ".join(s.text.strip() for s in segments).strip()
        return {"text": text, "ms": int((time.perf_counter() - t0) * 1000)}

    # ---- status
    def status(self) -> dict:
        self._ensure_tts_thread(timeout=2.0)
        ok, why = self._stt_ok()
        eng = self._tts_engine
        st = {
            "tts": eng is not None,
            "tts_engine": getattr(eng, "name", None),
            "tts_error": "" if eng else (self._tts_error or "Fala indisponível."),
            "voices": list(self._voices),
            "voice": self.default_voice or pick_voice(self._voices),
            "speaking": self._speaking.is_set(),
            "stt": ok and self._model is not None,
            "stt_available": ok,
            "stt_engine": "faster-whisper" if ok else None,
            "stt_model": self.model_name,
            "stt_installed": self._model is not None or self._dir_size() > 0,
            "stt_error": why,
            "stt_setup": dict(self._stt_state),
            "max_seconds": self.max_seconds,
        }
        return st


# ---------------------------------------------------------------- JS para a interface
RECORDER_JS = r"""
// Gravador Djorge: 16 kHz mono PCM -> WAV -> base64 -> POST /api/voice/transcribe.
// Uso:
//   const rec = await DjorgeRecorder.start({onLevel: v => anim(v)});   // v em 0..1
//   const {text} = await rec.stop();      // para, envia e devolve {text, ms} (ou {error})
//   rec.cancel();                          // descarta sem enviar
// Limite: 60 s (para sozinho). Nada é gravado em disco; o áudio vai só para o servidor local.
window.DjorgeRecorder = (() => {
  const RATE = 16000, MAX_S = 60;
  const WORKLET = `class P extends AudioWorkletProcessor{
    constructor(o){super();this.r=o.processorOptions.ratio;this.t=0;}
    process(i){const c=i[0]&&i[0][0];if(!c)return true;const out=[];let s=0;
      for(let k=0;k<c.length;k++){s+=c[k]*c[k];}
      for(;this.t<c.length;this.t+=this.r)out.push(c[Math.floor(this.t)]);
      this.t-=c.length;this.port.postMessage({pcm:new Float32Array(out),rms:Math.sqrt(s/c.length)});
      return true;}}
    registerProcessor('djorge-rec',P);`;
  function wav(chunks){
    const n=chunks.reduce((a,c)=>a+c.length,0), b=new DataView(new ArrayBuffer(44+n*2));
    const w=(o,s)=>{for(let i=0;i<s.length;i++)b.setUint8(o+i,s.charCodeAt(i));};
    w(0,'RIFF');b.setUint32(4,36+n*2,true);w(8,'WAVE');w(12,'fmt ');b.setUint32(16,16,true);
    b.setUint16(20,1,true);b.setUint16(22,1,true);b.setUint32(24,RATE,true);b.setUint32(28,RATE*2,true);
    b.setUint16(32,2,true);b.setUint16(34,16,true);w(36,'data');b.setUint32(40,n*2,true);
    let o=44;for(const c of chunks)for(let i=0;i<c.length;i++,o+=2){
      const v=Math.max(-1,Math.min(1,c[i]));b.setInt16(o,v<0?v*0x8000:v*0x7FFF,true);}
    return new Uint8Array(b.buffer);
  }
  function b64(u8){let s='';for(let i=0;i<u8.length;i+=0x8000)s+=String.fromCharCode.apply(null,u8.subarray(i,i+0x8000));return btoa(s);}
  async function start(opts={}){
    const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
    const ctx=new AudioContext();
    const url=URL.createObjectURL(new Blob([WORKLET],{type:'application/javascript'}));
    await ctx.audioWorklet.addModule(url);URL.revokeObjectURL(url);
    const src=ctx.createMediaStreamSource(stream);
    const node=new AudioWorkletNode(ctx,'djorge-rec',{processorOptions:{ratio:ctx.sampleRate/RATE}});
    const chunks=[];let total=0,done=false,timer;
    node.port.onmessage=e=>{if(done)return;chunks.push(e.data.pcm);total+=e.data.pcm.length;
      opts.onLevel&&opts.onLevel(Math.min(1,e.data.rms*4));
      if(total>=RATE*MAX_S){rec.stop().then(r=>opts.onAutoStop&&opts.onAutoStop(r));}};
    src.connect(node);
    const close=()=>{done=true;clearTimeout(timer);src.disconnect();node.disconnect();stream.getTracks().forEach(t=>t.stop());ctx.close();};
    const rec={
      cancel(){if(!done)close();},
      async stop(){if(done)return{text:''};close();
        const r=await fetch('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({audio_b64:b64(wav(chunks))})});
        return r.json();}
    };
    return rec;
  }
  // Alternativa quando status().stt é false: reconhecimento do WebView2, se existir.
  function browserRecognizer(){const R=window.SpeechRecognition||window.webkitSpeechRecognition;
    if(!R)return null;const r=new R();r.lang='pt-BR';r.interimResults=true;return r;}
  return {start, browserRecognizer};
})();
"""
