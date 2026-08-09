#!/usr/bin/env python3
"""
reel_compiler.py

Erzeugt aus einer JSON-Datei ein vertikales 9:16-Video mit FFmpeg.

Funktionen:
- Clips in beliebiger Reihenfolge
- Start/Ende pro Clip
- einheitliches 9:16-Format
- Text pro Clip: top / center / bottom
- einheitlicher Übergang: cut / fade / fadeblack / fadewhite / wipeleft / wiperight
- globaler Song für das gesamte Reel ODER Song pro Clip
- Lautstärke für jeden Song konfigurierbar
- Originalton pro Clip konfigurierbar
- Audio-Übergänge per acrossfade
- alles lokal; benötigt nur Python 3 und FFmpeg

Aufruf:
    python reel_compiler.py reel.json

FFmpeg muss im PATH liegen:
    ffmpeg -version
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


FPS = 30
WIDTH = 1080
HEIGHT = 1920


def die(msg: str) -> None:
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def ffmpeg_escape(value: str) -> str:
    """Escape für FFmpeg-Filter-Ausdrücke."""
    return (
        str(value)
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace(";", r"\;")
    )


def parse_time(value) -> float:
    """Akzeptiert Sekunden oder HH:MM:SS(.mmm) bzw. MM:SS(.mmm)."""
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if ":" not in s:
        return float(s)

    parts = s.split(":")
    if len(parts) == 2:
        m, sec = parts
        return int(m) * 60 + float(sec)
    if len(parts) == 3:
        h, m, sec = parts
        return int(h) * 3600 + int(m) * 60 + float(sec)

    raise ValueError(f"Ungültige Zeit: {value}")


def run(cmd: list[str]) -> None:
    print("\n$", " ".join(shlex.quote(x) for x in cmd))
    p = subprocess.run(cmd)
    if p.returncode != 0:
        die("FFmpeg ist mit einem Fehler beendet worden.")


def check_ffmpeg() -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception:
        die(
            "FFmpeg wurde nicht gefunden. Installiere FFmpeg und stelle sicher, "
            "dass 'ffmpeg' im PATH liegt."
        )


def make_text_file(tmpdir: Path, index: int, text: str) -> Path:
    p = tmpdir / f"text_{index:03d}.txt"
    # UTF-8 BOM hilft FFmpeg unter Windows bei manchen Unicode-Schriften.
    p.write_text("\ufeff" + text, encoding="utf-8")
    return p


def font_path(settings: dict, base: Path) -> str:
    """
    Ermittelt die zu verwendende Schrift.

    'font' kann absolut oder relativ zur config.json
    angegeben werden.
    """

    p = settings.get("font")

    if p:
        font = Path(str(p))

        if not font.is_absolute():
            font = base / font

        font = font.resolve()

        print(f"\nFont: {font}")

        if not font.exists():
            die(
                f"Die konfigurierte Schriftart wurde nicht gefunden: "
                f"{font}"
            )

        return str(font)

    # Häufige Defaults
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ]

    for candidate in candidates:
        if Path(candidate).exists():
            print(f"\nFont: {candidate}")
            return candidate

    return ""


def validate_clip(clip: dict, index: int, base: Path) -> tuple[Path, float, float]:
    if "file" not in clip:
        die(f"Clip {index}: 'file' fehlt.")
    if "start" not in clip or "end" not in clip:
        die(f"Clip {index}: 'start' und 'end' sind erforderlich.")

    path = Path(clip["file"])
    if not path.is_absolute():
        path = base / path

    if not path.exists():
        die(f"Clip {index}: Datei nicht gefunden: {path}")

    try:
        start = parse_time(clip["start"])
        end = parse_time(clip["end"])
    except ValueError as e:
        die(f"Clip {index}: {e}")

    if start < 0 or end <= start:
        die(f"Clip {index}: Ende muss größer als Start sein.")

    return path, start, end


def build(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent

    settings = config.get("settings", {})
    clips = config.get("clips", [])

    if not clips:
        die("Keine clips in der JSON-Datei.")

    output = Path(config.get("output", "reel.mp4"))
    if not output.is_absolute():
        output = base / output

    transition = settings.get("transition", "fade").lower()
    transition_duration = float(settings.get("transition_duration", 0.25))
    source_volume_default = float(settings.get("source_volume", 1.0))

    allowed_transitions = {
        "cut": 0.0,
        "fade": transition_duration,
        "fadeblack": transition_duration,
        "fadewhite": transition_duration,
        "wipeleft": transition_duration,
        "wiperight": transition_duration,
        "wipeup": transition_duration,
        "wipedown": transition_duration,
        "slideleft": transition_duration,
        "slideright": transition_duration,
        "slideup": transition_duration,
        "slidedown": transition_duration,
    }
    if transition not in allowed_transitions:
        die(f"Unbekannter Übergang: {transition}")

    global_music = settings.get("music")
    if global_music:
        global_music = str((base / global_music) if not Path(global_music).is_absolute() else Path(global_music))
        if not Path(global_music).exists():
            die(f"Globaler Song nicht gefunden: {global_music}")

    out_w = int(settings.get("width", WIDTH))
    out_h = int(settings.get("height", HEIGHT))
    fps = int(settings.get("fps", FPS))

    text_cfg = settings.get("text_style", {})
    font = font_path(settings, base)
    if not font:
        die(
            "Keine Schriftart gefunden. Setze z.B. settings.font auf "
            "C:\\Windows\\Fonts\\arial.ttf."
        )

    fontsize = int(text_cfg.get("fontsize", 64))
    fontcolor = text_cfg.get("fontcolor", "white")
    boxcolor = text_cfg.get("boxcolor", "black@0.45")
    box = int(text_cfg.get("box", 1))
    boxborder = int(text_cfg.get("boxborderw", 18))
    margin_x = int(text_cfg.get("margin_x", 60))
    margin_y = int(text_cfg.get("margin_y", 100))

    # Input- und Segmentdaten vorbereiten.
    prepared = []
    total_duration = 0.0

    for i, clip in enumerate(clips, 1):
        path, start, end = validate_clip(clip, i, base)
        duration = end - start
        if duration <= 0.05:
            die(f"Clip {i} ist zu kurz.")

        song = clip.get("music")
        if song:
            song_path = Path(song)
            if not song_path.is_absolute():
                song_path = base / song_path
            if not song_path.exists():
                die(f"Clip {i}: Song nicht gefunden: {song_path}")
            song = str(song_path)

        prepared.append(
            {
                "path": str(path),
                "start": start,
                "end": end,
                "duration": duration,
                "text": clip.get("text", ""),
                "text_position": clip.get("text_position", "bottom").lower(),
                "source_volume": float(clip.get("source_volume", source_volume_default)),
                "music": song,
                "music_volume": float(clip.get("music_volume", 0.0 if not song else 0.18)),
            }
        )
        total_duration += duration

    # Textpositionen.
    def text_y(position: str) -> str:
        if position == "top":
            return str(margin_y)
        if position in ("center", "middle"):
            return "(h-text_h)/2"
        if position == "bottom":
            return f"h-text_h-{margin_y}"
        die(f"Ungültige Textposition: {position}. Erlaubt: top, center, bottom.")

    with tempfile.TemporaryDirectory(prefix="reel_compiler_") as td:
        tmpdir = Path(td)

        cmd = ["ffmpeg", "-y"]
        filter_parts = []
        video_labels = []
        audio_labels = []

        # Für jeden Clip ein eigener Input.
        for i, item in enumerate(prepared):
            cmd += [
                "-ss", str(item["start"]),
                "-to", str(item["end"]),
                "-i", item["path"],
            ]

        # Musik-Inputs:
        # Globaler Song wird einmal als zusätzlicher Input angehängt.
        # Clip-spezifische Songs bekommen je einen eigenen Input.
        music_input_indices = {}
        next_input = len(prepared)

        if global_music:
            cmd += ["-stream_loop", "-1", "-i", global_music]
            music_input_indices["__global__"] = next_input
            next_input += 1

        for i, item in enumerate(prepared):
            if item["music"]:
                cmd += ["-stream_loop", "-1", "-i", item["music"]]
                music_input_indices[i] = next_input
                next_input += 1

        # Video + Originalton jedes Clips.
        for i, item in enumerate(prepared):
            vin = f"{i}:v"
            ain = f"{i}:a"

            # Letterbox/crop auf exakt 9:16, danach FPS.
            # force_original_aspect_ratio=decrease sorgt dafür, dass nichts
            # verzerrt wird; der Rest wird mit schwarzem Hintergrund gefüllt.
            vfilter = (
                f"[{vin}]"
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,fps={fps},format=yuv420p"
            )

            text = item["text"]
            if text:
                textfile = make_text_file(tmpdir, i, text)
                y = text_y(item["text_position"])
                # textfile vermeidet die meisten Probleme mit Sonderzeichen.
                vfilter += (
                    f",drawtext=fontfile='{ffmpeg_escape(font)}'"
                    f":textfile='{ffmpeg_escape(str(textfile))}'"
                    f":fontcolor={ffmpeg_escape(fontcolor)}"
                    f":fontsize={fontsize}"
                    f":x=(w-text_w)/2:y={y}"
                    f":box={box}:boxcolor={ffmpeg_escape(boxcolor)}"
                    f":boxborderw={boxborder}"
                )

            vfilter += f"[v{i}]"
            filter_parts.append(vfilter)

            # Originalton optional. Nicht jeder Clip muss Audio haben.
            # aevalsrc=0 als Fallback bei stummen Videos.
            source_vol = item["source_volume"]
            filter_parts.append(
                f"[{ain}]aresample=48000,volume={source_vol},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )

            video_labels.append(f"[v{i}]")
            audio_labels.append(f"[a{i}]")

        # Video-Übergänge.
        current_v = "v0"
        accumulated = prepared[0]["duration"]
        for i in range(1, len(prepared)):
            out_label = f"vx{i}"
            d = allowed_transitions[transition]

            if d == 0:
                filter_parts.append(
                    f"[{current_v}][v{i}]concat=n=2:v=1:a=0[{out_label}]"
                )
                accumulated += prepared[i]["duration"]
            else:
                offset = max(0.0, accumulated - d)
                filter_parts.append(
                    f"[{current_v}][v{i}]"
                    f"xfade=transition={transition}:duration={d}:offset={offset}"
                    f"[{out_label}]"
                )
                accumulated += prepared[i]["duration"] - d

            current_v = out_label

        final_video = current_v

        # Audio-Übergänge für Originalton der Clips.
        current_a = "a0"
        audio_accumulated = prepared[0]["duration"]

        for i in range(1, len(prepared)):
            out_label = f"ax{i}"
            d = allowed_transitions[transition]
            if d == 0:
                filter_parts.append(
                    f"[{current_a}][a{i}]concat=n=2:v=0:a=1[{out_label}]"
                )
                audio_accumulated += prepared[i]["duration"]
            else:
                d = min(d, prepared[i]["duration"] / 2, audio_accumulated / 2)
                filter_parts.append(
                    f"[{current_a}][a{i}]"
                    f"acrossfade=d={d}:c1=tri:c2=tri[{out_label}]"
                )
                audio_accumulated += prepared[i]["duration"] - d
            current_a = out_label

        final_audio = current_a

        # Musik:
        # 1) globaler Song: einmal über das gesamte Reel legen.
        # 2) pro Clip: Musik wird innerhalb des jeweiligen Clips mit dem
        #    Originalton gemischt, anschließend werden die Clip-Audios
        #    ineinander überblendet.
        if global_music:
            mi = music_input_indices["__global__"]
            global_vol = float(settings.get("music_volume", 0.18))
            filter_parts.append(
                f"[{mi}:a]aresample=48000,volume={global_vol},"
                f"atrim=duration={audio_accumulated},asetpts=PTS-STARTPTS[gm]"
            )
            filter_parts.append(
                f"[{final_audio}][gm]amix=inputs=2:duration=first:dropout_transition=0,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
            )
            final_audio = "aout"

        else:
            # Pro Clip Musik in den jeweiligen Clip-Ton mischen.
            # Danach Audio erneut zusammensetzen.
            mixed_labels = []
            for i, item in enumerate(prepared):
                if i in music_input_indices:
                    mi = music_input_indices[i]
                    vol = item["music_volume"]
                    label = f"m{i}"
                    filter_parts.append(
                        f"[{mi}:a]aresample=48000,volume={vol},"
                        f"atrim=duration={item['duration']},"
                        f"asetpts=PTS-STARTPTS[{label}]"
                    )
                    mix = f"mx{i}"
                    filter_parts.append(
                        f"[a{i}][{label}]amix=inputs=2:duration=longest:"
                        f"dropout_transition=0[{mix}]"
                    )
                    mixed_labels.append(f"[{mix}]")
                else:
                    mixed_labels.append(f"[a{i}]")

            current_m = mixed_labels[0][1:-1]
            mixed_accum = prepared[0]["duration"]
            for i in range(1, len(prepared)):
                out_label = f"mxall{i}"
                d = allowed_transitions[transition]
                if d == 0:
                    filter_parts.append(
                        f"[{current_m}]{mixed_labels[i]}"
                        f"concat=n=2:v=0:a=1[{out_label}]"
                    )
                    mixed_accum += prepared[i]["duration"]
                else:
                    d = min(d, prepared[i]["duration"] / 2, mixed_accum / 2)
                    filter_parts.append(
                        f"[{current_m}]{mixed_labels[i]}"
                        f"acrossfade=d={d}:c1=tri:c2=tri[{out_label}]"
                    )
                    mixed_accum += prepared[i]["duration"] - d
                current_m = out_label

            final_audio = current_m

        filter_complex = ";".join(filter_parts)

        cmd += [
            "-filter_complex", filter_complex,
            "-map", f"[{final_video}]",
            "-map", f"[{final_audio}]",
            "-c:v", "libx264",
            "-preset", settings.get("preset", "medium"),
            "-crf", str(settings.get("crf", 20)),
            "-c:a", "aac",
            "-b:a", settings.get("audio_bitrate", "192k"),
            "-movflags", "+faststart",
            "-shortest",
            str(output),
        ]

        print(f"\nErzeuge: {output}")
        run(cmd)
        print(f"\nFERTIG: {output}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Aufruf: python reel_compiler.py reel.json")
        sys.exit(2)

    check_ffmpeg()

    config_path = Path(sys.argv[1]).resolve()
    if not config_path.exists():
        die(f"Konfigurationsdatei nicht gefunden: {config_path}")

    build(config_path)


if __name__ == "__main__":
    main()

