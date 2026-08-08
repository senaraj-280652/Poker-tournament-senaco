# -*- coding: utf-8 -*-
"""
Génère et joue un signal sonore (bip) de tonalité et durée réglables, pour
le "Signal de mouvements" (paramètres). N'utilise que la bibliothèque
standard : le fichier .wav est généré localement (onde sinusoïdale) puis
lu de façon asynchrone via la commande de lecture audio du système
(afplay sur macOS, winsound sur Windows, paplay/aplay sur Linux). Si rien
de tout ça n'est disponible, l'appelant peut retomber sur le bip système
Tkinter (root.bell()).
"""
import math
import os
import struct
import subprocess
import sys
import wave

_SAMPLE_RATE = 44100
_cache = {"key": None, "path": None}


def _signal_dir():
    d = os.path.join(os.path.expanduser("~"), ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d


def _generate_tone_wav(path, frequency_hz, duration_ms, volume=0.5):
    """Écrit un .wav mono 16 bits contenant une tonalité pure, avec un
    court fondu d'entrée/sortie pour éviter les "clics" audibles."""
    frequency_hz = max(50, min(8000, int(frequency_hz)))
    duration_ms = max(50, min(5000, int(duration_ms)))
    n_samples = int(_SAMPLE_RATE * duration_ms / 1000)
    amplitude = int(32767 * volume)
    fade = max(1, min(400, n_samples // 10))

    frames = bytearray()
    for i in range(n_samples):
        t = i / _SAMPLE_RATE
        value = math.sin(2 * math.pi * frequency_hz * t)
        if i < fade:
            value *= i / fade
        elif i > n_samples - fade:
            value *= (n_samples - i) / fade
        frames += struct.pack("<h", int(amplitude * value))

    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(bytes(frames))


def play_tone(frequency_hz, duration_ms):
    """Joue un bip de fréquence (Hz) et durée (ms) données, sans bloquer
    l'interface. Renvoie True si la lecture a pu être lancée."""
    try:
        key = (int(frequency_hz), int(duration_ms))
    except (TypeError, ValueError):
        return False
    try:
        if _cache["key"] != key or not _cache["path"] or not os.path.exists(_cache["path"]):
            path = os.path.join(_signal_dir(), "movement_signal.wav")
            _generate_tone_wav(path, *key)
            _cache["key"] = key
            _cache["path"] = path
        path = _cache["path"]

        if sys.platform == "darwin":
            subprocess.Popen(
                ["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        elif sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        else:
            for player in ("paplay", "aplay"):
                try:
                    subprocess.Popen(
                        [player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return True
                except FileNotFoundError:
                    continue
    except Exception:
        pass
    return False
