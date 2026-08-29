#!/usr/bin/env python3

"""
reel_compiler.py

Erzeugt aus einer JSON-Konfiguration ein fertiges Reel.

Funktionen
----------
- Clips in beliebiger Reihenfolge
- Start/Ende pro Clip
- end = "00:00" bedeutet: bis zum Ende des Quellvideos
- Ausgabeauflösung und FPS konfigurierbar
- Text pro Clip als Array
- beliebig viele Textzeilen
- Textposition: top / center / bottom
- Textzeilen werden einzeln gerendert
- keine temporären Textdateien
- einheitlicher Übergang zwischen Clips
- Originalton pro Clip konfigurierbar
- Videos ohne Audiospur werden automatisch mit Stille versehen
- globale Musik für das komplette Reel
- oder Musik pro Clip
- music_start zum Überspringen des Anfangs eines Songs
- Musiklautstärke konfigurierbar
- vollständig lokal

Aufruf
------
    python reel_compiler.py reel.json

Voraussetzung
-------------
FFmpeg und FFprobe müssen im PATH verfügbar sein.

Test:
    ffmpeg -version
    ffprobe -version
"""


from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path


# ============================================================
# Defaults
# ============================================================

DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_FPS = 30

DEFAULT_FONT_SIZE = 64
DEFAULT_FONT_COLOR = "white"
DEFAULT_BOX_COLOR = "black@0.45"
DEFAULT_BOX = 1
DEFAULT_BOX_BORDER = 18

DEFAULT_MARGIN_X = 60
DEFAULT_MARGIN_Y = 100

DEFAULT_TRANSITION = "fade"
DEFAULT_TRANSITION_DURATION = 0.25

DEFAULT_SOURCE_VOLUME = 1.0

DEFAULT_MUSIC_VOLUME = 0.18

DEFAULT_PRESET = "medium"
DEFAULT_CRF = 20
DEFAULT_AUDIO_BITRATE = "192k"


# ============================================================
# Fehler
# ============================================================

def die(message: str) -> None:
    print()
    print(f"FEHLER: {message}", file=sys.stderr)
    sys.exit(1)


# ============================================================
# FFmpeg
# ============================================================

def run_command(command: list[str]) -> None:
    print()
    print("$")
    print(
        " ".join(
            shlex.quote(str(x))
            for x in command
        )
    )
    print()

    result = subprocess.run(command)

    if result.returncode != 0:
        die("FFmpeg ist mit einem Fehler beendet worden.")


def check_ffmpeg() -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

    except Exception:
        die(
            "FFmpeg/FFprobe wurde nicht gefunden. "
            "Bitte FFmpeg installieren und sicherstellen, "
            "dass ffmpeg und ffprobe im PATH liegen."
        )


# ============================================================
# FFprobe
# ============================================================

def ffprobe(
    arguments: list[str],
) -> str:

    command = [
        "ffprobe",
        "-v",
        "error",
        *arguments,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
        )

        return result.stdout.strip()

    except subprocess.CalledProcessError as exc:
        die(
            "FFprobe konnte eine Mediendatei nicht analysieren."
        )

    return ""


def get_duration(path: Path) -> float:

    result = ffprobe(
        [
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )

    try:
        return float(result)

    except ValueError:
        die(
            f"Videodauer konnte nicht ermittelt werden:\n{path}"
        )

    return 0.0


def has_audio(path: Path) -> bool:

    result = ffprobe(
        [
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )

    return bool(result.strip())


# ============================================================
# Zeit
# ============================================================

def parse_time(value) -> float:
    """
    Unterstützt:

        5
        5.5
        "00:05"
        "01:15"
        "01:15.5"
        "00:01:15"
        "00:01:15.500"
    """

    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()

    if not value:
        raise ValueError(
            "Leere Zeitangabe."
        )

    if ":" not in value:
        return float(value)

    parts = value.split(":")

    if len(parts) == 2:

        minutes = int(parts[0])
        seconds = float(parts[1])

        return (
            minutes * 60
            + seconds
        )

    if len(parts) == 3:

        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])

        return (
            hours * 3600
            + minutes * 60
            + seconds
        )

    raise ValueError(
        f"Ungültige Zeitangabe: {value}"
    )


# ============================================================
# Pfade
# ============================================================

def resolve_path(
    value: str | None,
    base: Path,
) -> Path | None:

    if value is None:
        return None

    path = Path(str(value))

    if not path.is_absolute():
        path = base / path

    return path.resolve()


# ============================================================
# FFmpeg Escaping
# ============================================================

def escape_filter_value(value: str) -> str:
    """
    Escaping für Werte innerhalb eines FFmpeg-Filters.
    """

    return (
        str(value)
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace(",", r"\,")
        .replace(";", r"\;")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("'", r"\'")
    )


def escape_drawtext_text(text: str) -> str:
    """
    Escaping für drawtext:text.

    Wichtig:
    Es werden KEINE Zeilenumbrüche erzeugt.
    Jede Textzeile bekommt später ihren eigenen
    drawtext-Filter.

    Dadurch können CR/LF/BOM-Probleme nicht auftreten.
    """

    text = str(text)

    # Eventuelle Steuerzeichen entfernen.
    text = text.replace("\r", "")
    text = text.replace("\n", "")
    text = text.replace("\ufeff", "")

    return (
        text
        .replace("\\", r"\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace(",", r"\,")
        .replace(";", r"\;")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("%", r"\%")
    )


# ============================================================
# Font
# ============================================================

def get_font_path(
    settings: dict,
    base: Path,
) -> str:

    configured = settings.get("font")

    if configured:

        path = resolve_path(
            configured,
            base,
        )

        if path is None or not path.exists():

            die(
                "Die in settings.font angegebene "
                f"Schriftart wurde nicht gefunden:\n{path}"
            )

        print(
            f"Schriftart: {path}"
        )

        return str(path)

    # Linux-Defaults
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    for candidate in candidates:

        path = Path(candidate)

        if path.exists():

            print(
                f"Schriftart: {path}"
            )

            return str(path)

    die(
        "Keine Schriftart gefunden. "
        "Bitte settings.font in der config.json setzen."
    )

    return ""


# ============================================================
# Text
# ============================================================

def normalize_text(
    text,
) -> list[str]:

    if text is None:
        return []

    if isinstance(text, str):

        # Aus Kompatibilitätsgründen akzeptieren wir einen
        # einzelnen String weiterhin.
        #
        # Für die eigentliche Konfiguration wird aber ein
        # Array empfohlen.
        return [text]

    if not isinstance(text, list):

        raise ValueError(
            "'text' muss ein Array sein."
        )

    result = []

    for line in text:

        line = str(line)

        # Steuerzeichen entfernen.
        line = line.replace("\r", "")
        line = line.replace("\n", "")
        line = line.replace("\ufeff", "")

        result.append(line)

    return result


def add_text_filters(
    filters: list[str],
    input_label: str,
    output_label: str,
    text,
    position: str,
    font: str,
    fontsize: int,
    fontcolor: str,
    box: int,
    boxcolor: str,
    boxborderw: int,
    margin_x: int,
    margin_y: int,
) -> str:
    """
    Fügt für jede Textzeile einen eigenen drawtext-Filter hinzu.

    Dadurch gibt es keine Probleme mit:
        - CR
        - LF
        - BOM
        - FFmpeg textfile

    Alle Zeilen werden horizontal zentriert.

    top / center / bottom beziehen sich auf den kompletten
    Textblock.
    """

    lines = normalize_text(text)

    if not lines:

        filters.append(
            f"{input_label}copy"
            f"[{output_label}]"
        )

        return output_label

    position = position.lower()

    if position == "middle":
        position = "center"

    if position not in (
        "top",
        "center",
        "bottom",
    ):

        raise ValueError(
            f"Ungültige Textposition: {position}"
        )

    # --------------------------------------------------------
    # Geschätzte Zeilenhöhe.
    #
    # drawtext verwendet intern eine etwas größere Höhe als
    # die reine fontsize.  1.25 ist ein guter Wert für normale
    # Schriftarten.
    # --------------------------------------------------------

    line_height = int(
        fontsize * 1.4
    )

    line_count = len(lines)

    total_height = (
        line_count * line_height
    )

    # --------------------------------------------------------
    # Position des gesamten Blocks
    # --------------------------------------------------------

    if position == "top":

        block_y = (
            margin_y
        )

    elif position == "center":

        block_y = (
            f"(h-{total_height})/2"
        )

    else:

        block_y = (
            f"h-{total_height}-{margin_y}"
        )

    current = input_label

    # --------------------------------------------------------
    # Jede Zeile bekommt ihren eigenen drawtext-Filter.
    # --------------------------------------------------------

    for index, line in enumerate(lines):

        escaped = escape_drawtext_text(
            line
        )

        if position == "center":

            y = (
                f"({block_y})+"
                f"{index * line_height}"
            )

        else:

            y = (
                f"({block_y})+"
                f"{index * line_height}"
            )

        # Letzte Zeile bekommt direkt den gewünschten
        # output_label.
        if index == len(lines) - 1:

            next_label = output_label

        else:

            next_label = (
                f"text_{output_label}_{index}"
            )

        drawtext = (
            f"[{current}]"
            f"drawtext="
            f"fontfile='{escape_filter_value(font)}'"
            f":text='{escaped}'"
            f":fontcolor={escape_filter_value(fontcolor)}"
            f":fontsize={fontsize}"
            f":x=(w-text_w)/2"
            f":y={y}"
            f":box={box}"
            f":boxcolor={escape_filter_value(boxcolor)}"
            f":boxborderw={boxborderw}"
            f"[{next_label}]"
        )

        filters.append(
            drawtext
        )

        current = next_label

    return output_label


# ============================================================
# Clip validieren
# ============================================================

def prepare_clip(
    clip: dict,
    index: int,
    base: Path,
) -> dict:

    if "file" not in clip:

        die(
            f"Clip {index}: 'file' fehlt."
        )

    if "start" not in clip:

        die(
            f"Clip {index}: 'start' fehlt."
        )

    if "end" not in clip:

        die(
            f"Clip {index}: 'end' fehlt."
        )

    path = resolve_path(
        clip["file"],
        base,
    )

    if path is None or not path.exists():

        die(
            f"Clip {index}: Datei nicht gefunden:\n{path}"
        )

    try:

        start = parse_time(
            clip["start"]
        )

        end = parse_time(
            clip["end"]
        )

    except ValueError as exc:

        die(
            f"Clip {index}: {exc}"
        )

    duration = get_duration(
        path
    )

    # --------------------------------------------------------
    # end == 00:00
    #
    # bedeutet:
    # bis zum tatsächlichen Ende des Videos.
    # --------------------------------------------------------

    if end == 0:

        end = duration

    if start < 0:

        die(
            f"Clip {index}: start darf nicht negativ sein."
        )

    if start >= duration:

        die(
            f"Clip {index}: start liegt hinter dem Ende "
            f"des Videos ({duration:.3f}s)."
        )

    if end > duration:

        # Kleine Rundungsabweichungen tolerieren.
        if end - duration < 0.1:

            end = duration

        else:

            die(
                f"Clip {index}: end ({end:.3f}s) liegt "
                f"hinter dem Ende des Videos "
                f"({duration:.3f}s)."
            )

    if end <= start:

        die(
            f"Clip {index}: end muss größer als start sein."
        )

    clip_duration = (
        end - start
    )

    if clip_duration < 0.05:

        die(
            f"Clip {index}: Der Ausschnitt ist zu kurz."
        )

    audio = has_audio(
        path
    )

    # --------------------------------------------------------
    # Text
    # --------------------------------------------------------

    try:

        text = normalize_text(
            clip.get(
                "text",
                [],
            )
        )

    except ValueError as exc:

        die(
            f"Clip {index}: {exc}"
        )

    text_position = str(
        clip.get(
            "text_position",
            "bottom",
        )
    ).lower()

    if text_position == "middle":

        text_position = "center"

    if text_position not in (
        "top",
        "center",
        "bottom",
    ):

        die(
            f"Clip {index}: Ungültige text_position "
            f"'{text_position}'. "
            f"Erlaubt: top, center, bottom."
        )

    # --------------------------------------------------------
    # Musik
    # --------------------------------------------------------

    music = clip.get(
        "music"
    )

    if music:

        music_path = resolve_path(
            music,
            base,
        )

        if music_path is None or not music_path.exists():

            die(
                f"Clip {index}: Musikdatei nicht gefunden:\n"
                f"{music_path}"
            )

        music = str(
            music_path
        )

    music_start = clip.get(
        "music_start",
        0,
    )

    try:

        music_start = parse_time(
            music_start
        )

    except ValueError as exc:

        die(
            f"Clip {index}: Ungültiges music_start: {exc}"
        )

    return {
        "path": path,
        "start": start,
        "end": end,
        "duration": clip_duration,
        "has_audio": audio,
        "text": text,
        "text_position": text_position,

        "source_volume": float(
            clip.get(
                "source_volume",
                DEFAULT_SOURCE_VOLUME,
            )
        ),

        "music": music,

        "music_start": music_start,

        "music_volume": float(
            clip.get(
                "music_volume",
                DEFAULT_MUSIC_VOLUME,
            )
        ),
    }


# ============================================================
# Main Build
# ============================================================

def build(
    config_path: Path,
) -> None:

    # --------------------------------------------------------
    # JSON laden
    # --------------------------------------------------------

    try:

        config = json.loads(
            config_path.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        die(
            f"Config konnte nicht gelesen werden:\n{exc}"
        )

    base = config_path.parent

    settings = config.get(
        "settings",
        {}
    )

    clips_config = config.get(
        "clips",
        []
    )

    if not isinstance(
        clips_config,
        list,
    ) or not clips_config:

        die(
            "Die Config enthält keine Clips."
        )

    # ========================================================
    # Allgemeine Einstellungen
    # ========================================================

    width = int(
        settings.get(
            "width",
            DEFAULT_WIDTH,
        )
    )

    height = int(
        settings.get(
            "height",
            DEFAULT_HEIGHT,
        )
    )

    fps = int(
        settings.get(
            "fps",
            DEFAULT_FPS,
        )
    )

    # ========================================================
    # Text
    # ========================================================

    text_style = settings.get(
        "text_style",
        {}
    )

    fontsize = int(
        text_style.get(
            "fontsize",
            DEFAULT_FONT_SIZE,
        )
    )

    fontcolor = str(
        text_style.get(
            "fontcolor",
            DEFAULT_FONT_COLOR,
        )
    )

    boxcolor = str(
        text_style.get(
            "boxcolor",
            DEFAULT_BOX_COLOR,
        )
    )

    box = int(
        text_style.get(
            "box",
            DEFAULT_BOX,
        )
    )

    boxborderw = int(
        text_style.get(
            "boxborderw",
            DEFAULT_BOX_BORDER,
        )
    )

    margin_x = int(
        text_style.get(
            "margin_x",
            DEFAULT_MARGIN_X,
        )
    )

    margin_y = int(
        text_style.get(
            "margin_y",
            DEFAULT_MARGIN_Y,
        )
    )

    font = get_font_path(
        settings,
        base,
    )

    # ========================================================
    # Übergang
    # ========================================================

    transition = str(
        settings.get(
            "transition",
            DEFAULT_TRANSITION,
        )
    ).lower()

    transition_duration = float(
        settings.get(
            "transition_duration",
            DEFAULT_TRANSITION_DURATION,
        )
    )

    supported_transitions = {
        "cut",
        "fade",
        "fadeblack",
        "fadewhite",
        "wipeleft",
        "wiperight",
        "wipeup",
        "wipedown",
        "slideleft",
        "slideright",
        "slideup",
        "slidedown",
    }

    if transition not in supported_transitions:

        die(
            f"Unbekannter Übergang '{transition}'.\n"
            f"Erlaubt: "
            f"{', '.join(sorted(supported_transitions))}"
        )

    # ========================================================
    # Globale Musik
    # ========================================================

    global_music = settings.get(
        "music"
    )

    global_music_start = parse_time(
        settings.get(
            "music_start",
            0,
        )
    )

    global_music_volume = float(
        settings.get(
            "music_volume",
            DEFAULT_MUSIC_VOLUME,
        )
    )

    if global_music:

        global_music_path = resolve_path(
            global_music,
            base,
        )

        if (
            global_music_path is None
            or not global_music_path.exists()
        ):

            die(
                f"Globale Musikdatei nicht gefunden:\n"
                f"{global_music_path}"
            )

        global_music = str(
            global_music_path
        )

    # ========================================================
    # Ausgabe
    # ========================================================

    output = config.get(
        "output",
        "reel.mp4",
    )

    output_path = resolve_path(
        output,
        base,
    )

    if output_path is None:

        die(
            "Ungültiger output-Pfad."
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # Clips vorbereiten
    # ========================================================

    clips = []

    for index, clip_config in enumerate(
        clips_config,
        start=1,
    ):

        print(
            f"Analysiere Clip {index}/{len(clips_config)}..."
        )

        clip = prepare_clip(
            clip_config,
            index,
            base,
        )

        clips.append(
            clip
        )

        print(
            f"  Datei:   {clip['path']}"
        )

        print(
            f"  Bereich: "
            f"{clip['start']:.3f}s - "
            f"{clip['end']:.3f}s"
        )

        print(
            f"  Länge:   "
            f"{clip['duration']:.3f}s"
        )

        print(
            f"  Audio:   "
            f"{'ja' if clip['has_audio'] else 'nein'}"
        )

    # ========================================================
    # FFmpeg Command
    # ========================================================

    command = [
        "ffmpeg",
        "-y",
    ]

    # ========================================================
    # Video Inputs
    # ========================================================

    for clip in clips:

        command += [
            "-ss",
            f"{clip['start']:.6f}",

            "-to",
            f"{clip['end']:.6f}",

            "-i",
            str(clip["path"]),
        ]

    # ========================================================
    # Stille-Audio Inputs
    #
    # Für jeden Clip ohne Audio wird ein eigener anullsrc-Input
    # angelegt.
    # ========================================================

    silent_indices = {}

    next_input_index = len(clips)

    for index, clip in enumerate(clips):

        if not clip["has_audio"]:

            command += [
                "-f",
                "lavfi",

                "-t",
                f"{clip['duration']:.6f}",

                "-i",
                "anullsrc="
                "channel_layout=stereo:"
                "sample_rate=48000",
            ]

            silent_indices[index] = (
                next_input_index
            )

            next_input_index += 1

    # ========================================================
    # Musik Inputs
    # ========================================================

    music_indices = {}

    # --------------------------------------------------------
    # Globale Musik
    # --------------------------------------------------------

    if global_music:

        command += [
            "-stream_loop",
            "-1",

            "-ss",
            f"{global_music_start:.6f}",

            "-i",
            global_music,
        ]

        music_indices["global"] = (
            next_input_index
        )

        next_input_index += 1

    # --------------------------------------------------------
    # Musik pro Clip
    # --------------------------------------------------------

    if not global_music:

        for index, clip in enumerate(clips):

            if clip["music"]:

                command += [
                    "-stream_loop",
                    "-1",

                    "-ss",
                    f"{clip['music_start']:.6f}",

                    "-i",
                    clip["music"],
                ]

                music_indices[index] = (
                    next_input_index
                )

                next_input_index += 1

    # ========================================================
    # Filter
    # ========================================================

    filters = []

    # ========================================================
    # Video Filters
    # ========================================================

    for index, clip in enumerate(clips):

        input_video = (
            f"{index}:v"
        )

        base_label = (
            f"scaled_{index}"
        )

        # ----------------------------------------------------
        # Einheitliches Format
        # ----------------------------------------------------

        filters.append(
            f"[{input_video}]"
            f"scale="
            f"{width}:{height}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad="
            f"{width}:{height}:"
            f"(ow-iw)/2:"
            f"(oh-ih)/2,"
            f"setsar=1,"
            f"fps={fps},"
            f"format=yuv420p"
            f"[{base_label}]"
        )

        # ----------------------------------------------------
        # Text
        # ----------------------------------------------------

        text = clip["text"]

        if text:

            try:

                add_text_filters(
                    filters=filters,
                    input_label=f"{base_label}",
                    output_label=f"v{index}",
                    text=text,
                    position=clip["text_position"],
                    font=font,
                    fontsize=fontsize,
                    fontcolor=fontcolor,
                    box=box,
                    boxcolor=boxcolor,
                    boxborderw=boxborderw,
                    margin_x=margin_x,
                    margin_y=margin_y,
                )

            except ValueError as exc:

                die(
                    f"Clip {index + 1}: {exc}"
                )

        else:

            filters.append(
                f"[{base_label}]"
                f"copy"
                f"[v{index}]"
            )

    # ========================================================
    # Audio Filters
    # ========================================================

    for index, clip in enumerate(clips):

        source_volume = (
            clip["source_volume"]
        )

        if clip["has_audio"]:

            audio_input = (
                f"{index}:a"
            )

            filters.append(
                f"[{audio_input}]"
                f"aresample=48000,"
                f"volume={source_volume},"
                f"atrim="
                f"duration={clip['duration']:.6f},"
                f"asetpts=PTS-STARTPTS"
                f"[a{index}]"
            )

        else:

            silent_index = (
                silent_indices[index]
            )

            filters.append(
                f"[{silent_index}:a]"
                f"aresample=48000,"
                f"volume={source_volume},"
                f"atrim="
                f"duration={clip['duration']:.6f},"
                f"asetpts=PTS-STARTPTS"
                f"[a{index}]"
            )

    # ========================================================
    # Video Übergänge
    # ========================================================

    current_video = "v0"

    current_video_duration = (
        clips[0]["duration"]
    )

    for index in range(
        1,
        len(clips),
    ):

        duration = min(
            transition_duration,
            clips[index]["duration"] / 2,
            current_video_duration / 2,
        )

        output_label = (
            f"vx{index}"
        )

        if transition == "cut":

            filters.append(
                f"[{current_video}]"
                f"[v{index}]"
                f"concat="
                f"n=2:"
                f"v=1:"
                f"a=0"
                f"[{output_label}]"
            )

            current_video_duration += (
                clips[index]["duration"]
            )

        else:

            offset = (
                current_video_duration
                - duration
            )

            filters.append(
                f"[{current_video}]"
                f"[v{index}]"
                f"xfade="
                f"transition={transition}:"
                f"duration={duration:.6f}:"
                f"offset={offset:.6f}"
                f"[{output_label}]"
            )

            current_video_duration += (
                clips[index]["duration"]
                - duration
            )

        current_video = (
            output_label
        )

    # ========================================================
    # Audio / Musik pro Clip
    # ========================================================

    if global_music:

        # ----------------------------------------------------
        # Zuerst den Originalton aller Clips mit ihren
        # Übergängen zusammenbauen.
        # ----------------------------------------------------

        current_audio = "a0"

        current_audio_duration = (
            clips[0]["duration"]
        )

        for index in range(
            1,
            len(clips),
        ):

            duration = min(
                transition_duration,
                clips[index]["duration"] / 2,
                current_audio_duration / 2,
            )

            output_label = (
                f"ax{index}"
            )

            if transition == "cut":

                filters.append(
                    f"[{current_audio}]"
                    f"[a{index}]"
                    f"concat="
                    f"n=2:"
                    f"v=0:"
                    f"a=1"
                    f"[{output_label}]"
                )

                current_audio_duration += (
                    clips[index]["duration"]
                )

            else:

                filters.append(
                    f"[{current_audio}]"
                    f"[a{index}]"
                    f"acrossfade="
                    f"d={duration:.6f}:"
                    f"c1=tri:"
                    f"c2=tri"
                    f"[{output_label}]"
                )

                current_audio_duration += (
                    clips[index]["duration"]
                    - duration
                )

            current_audio = (
                output_label
            )

        # ----------------------------------------------------
        # Globale Musik
        # ----------------------------------------------------

        music_input = (
            music_indices["global"]
        )

        filters.append(
            f"[{music_input}:a]"
            f"aresample=48000,"
            f"volume={global_music_volume},"
            f"atrim="
            f"duration={current_audio_duration:.6f},"
            f"asetpts=PTS-STARTPTS"
            f"[global_music]"
        )

        filters.append(
            f"[{current_audio}]"
            f"[global_music]"
            f"amix="
            f"inputs=2:"
            f"duration=first:"
            f"dropout_transition=0"
            f"[final_audio]"
        )

        final_audio = (
            "final_audio"
        )

    else:

        # ----------------------------------------------------
        # Musik pro Clip.
        #
        # Zuerst wird Musik jeweils in den einzelnen Clip
        # gemischt.
        # ----------------------------------------------------

        clip_audio_labels = []

        for index, clip in enumerate(clips):

            if index in music_indices:

                music_input = (
                    music_indices[index]
                )

                filters.append(
                    f"[{music_input}:a]"
                    f"aresample=48000,"
                    f"volume={clip['music_volume']},"
                    f"atrim="
                    f"duration={clip['duration']:.6f},"
                    f"asetpts=PTS-STARTPTS"
                    f"[music_{index}]"
                )

                filters.append(
                    f"[a{index}]"
                    f"[music_{index}]"
                    f"amix="
                    f"inputs=2:"
                    f"duration=first:"
                    f"dropout_transition=0"
                    f"[mixed_{index}]"
                )

                clip_audio_labels.append(
                    f"mixed_{index}"
                )

            else:

                clip_audio_labels.append(
                    f"a{index}"
                )

        # ----------------------------------------------------
        # Jetzt Clip-Audios mit Übergängen verbinden.
        # ----------------------------------------------------

        current_audio = (
            clip_audio_labels[0]
        )

        current_audio_duration = (
            clips[0]["duration"]
        )

        for index in range(
            1,
            len(clips),
        ):

            duration = min(
                transition_duration,
                clips[index]["duration"] / 2,
                current_audio_duration / 2,
            )

            output_label = (
                f"mx{index}"
            )

            if transition == "cut":

                filters.append(
                    f"[{current_audio}]"
                    f"[{clip_audio_labels[index]}]"
                    f"concat="
                    f"n=2:"
                    f"v=0:"
                    f"a=1"
                    f"[{output_label}]"
                )

                current_audio_duration += (
                    clips[index]["duration"]
                )

            else:

                filters.append(
                    f"[{current_audio}]"
                    f"[{clip_audio_labels[index]}]"
                    f"acrossfade="
                    f"d={duration:.6f}:"
                    f"c1=tri:"
                    f"c2=tri"
                    f"[{output_label}]"
                )

                current_audio_duration += (
                    clips[index]["duration"]
                    - duration
                )

            current_audio = (
                output_label
            )

        final_audio = (
            current_audio
        )

    # ========================================================
    # Filter Complex
    # ========================================================

    filter_complex = ";".join(
        filters
    )

    # ========================================================
    # Encoder
    # ========================================================

    preset = str(
        settings.get(
            "preset",
            DEFAULT_PRESET,
        )
    )

    crf = int(
        settings.get(
            "crf",
            DEFAULT_CRF,
        )
    )

    audio_bitrate = str(
        settings.get(
            "audio_bitrate",
            DEFAULT_AUDIO_BITRATE,
        )
    )

    # ========================================================
    # Finaler FFmpeg-Aufruf
    # ========================================================

    command += [
        "-filter_complex",
        filter_complex,

        "-map",
        f"[{current_video}]",

        "-map",
        f"[{final_audio}]",

        "-c:v",
        "libx264",

        "-preset",
        preset,

        "-crf",
        str(crf),

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        audio_bitrate,

        "-movflags",
        "+faststart",

        "-shortest",

        str(output_path),
    ]

    # ========================================================
    # Ausführen
    # ========================================================

    print()
    print("=" * 60)
    print("REEL COMPILER")
    print("=" * 60)

    print(
        f"Clips:       {len(clips)}"
    )

    print(
        f"Auflösung:   {width}x{height}"
    )

    print(
        f"FPS:         {fps}"
    )

    print(
        f"Übergang:    {transition}"
    )

    print(
        f"Übergangsdauer: {transition_duration}s"
    )

    print(
        f"Musik:       "
        f"{'global' if global_music else 'pro Clip'}"
    )

    print(
        f"Ausgabe:     {output_path}"
    )

    print("=" * 60)

    run_command(
        command
    )

    print()
    print("=" * 60)
    print("FERTIG")
    print("=" * 60)
    print()
    print(
        output_path
    )


# ============================================================
# Main
# ============================================================

def main() -> None:

    if len(sys.argv) != 2:

        print(
            "Aufruf:"
        )

        print(
            "    python reel_compiler.py reel.json"
        )

        sys.exit(2)

    check_ffmpeg()

    config_path = Path(
        sys.argv[1]
    ).resolve()

    if not config_path.exists():

        die(
            f"Konfigurationsdatei nicht gefunden:\n"
            f"{config_path}"
        )

    if config_path.suffix.lower() != ".json":

        print(
            "WARNUNG: Die Config hat keine .json-Endung."
        )

    build(
        config_path
    )


if __name__ == "__main__":
    main()
