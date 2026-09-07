#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


# ============================================================
# Helpers
# ============================================================

def die(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def run(cmd):
    print("\n>>", " ".join(shlex.quote(str(x)) for x in cmd))
    result = subprocess.run(cmd)

    if result.returncode != 0:
        die(f"FFmpeg beendet mit Fehlercode {result.returncode}")


def run_capture(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def require_program(name):
    result = subprocess.run(
        ["which", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if result.returncode != 0:
        die(f"{name} wurde nicht gefunden.")


def label(name):
    """
    FFmpeg-Filterlabel.

    Wichtig:
    Intern werden Labels OHNE [] gespeichert.
    Erst hier werden sie geklammert.
    """
    return f"[{name}]"


def ff_escape(value):
    text = str(value)

    # Steuerzeichen entfernen
    text = text.replace("\r", "").replace("\n", "").replace("\ufeff", "")

    # Erst Backslashes escapen
    text = text.replace("\\", r"\\")

    # FFmpeg-spezifische Trennzeichen & Leerzeichen escapen
    return (
        text
        .replace(" ", r"\ ")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace(";", r"\;")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace("%", r"\%")
    )


def parse_time(value):
    """
    Akzeptiert:
      5
      5.5
      00:05
      00:00:05
      00:00

    None / leer => None

    00:00 wird beim 'end'-Wert speziell behandelt:
    => Ende der Quelle
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()

    if not value:
        return None

    if ":" not in value:
        return float(value)

    parts = value.split(":")

    if len(parts) == 2:
        minutes = float(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds

    if len(parts) == 3:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])

        return hours * 3600 + minutes * 60 + seconds

    raise ValueError(f"Ungültige Zeitangabe: {value}")


def get_media_info(path):
    """
    Liefert:
      duration
      has_video
      has_audio
    """

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        str(path)
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        die(
            f"ffprobe konnte Datei nicht lesen:\n"
            f"{path}\n\n"
            f"{result.stderr}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        die(f"Ungültige ffprobe-Ausgabe für {path}")

    duration = float(
        data.get("format", {}).get("duration") or 0
    )

    streams = data.get("streams", [])

    has_video = any(
        s.get("codec_type") == "video"
        for s in streams
    )

    has_audio = any(
        s.get("codec_type") == "audio"
        for s in streams
    )

    return {
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
    }


def resolve_path(base_dir, filename):
    path = Path(filename)

    if not path.is_absolute():
        path = base_dir / path

    return path.resolve()


# ============================================================
# Text
# ============================================================

def normalize_text(text):
    """
    JSON text:
      "Hallo"

    oder:
      ["Hallo", "zweite Zeile"]

    oder:
      ["Hallo", "", "dritte Zeile"]

    Leere Zeilen werden entfernt.
    """

    if text is None:
        return []

    if isinstance(text, str):
        return [text]

    if isinstance(text, list):
        result = []

        for item in text:
            if item is None:
                continue

            item = str(item)

            if item.strip() == "":
                continue

            result.append(item)

        return result

    return [str(text)]


def get_text_y(position, line_count, fontsize, height, margin_y):
    """
    Position bezieht sich auf den GESAMTEN Textblock.

    top:
      Block oben

    center:
      Block vertikal zentriert

    bottom:
      Block unten
    """

    line_height = fontsize * 1.25
    block_height = line_count * line_height

    position = str(position or "center").lower()

    if position == "top":
        return margin_y

    if position == "bottom":
        return f"(h-{margin_y}-{block_height:.3f})"

    # center
    return f"((h-{block_height:.3f})/2)"


def add_text_filters(
    filters,
    input_label,
    output_label,
    text_lines,
    text_position,
    settings
):
    # WENN KEIN TEXT: Direkt umbenennen/durchreichen via null-Filter
    if not text_lines:
        filters.append(
            f"{label(input_label)}copy{label(output_label)}"
        )
        return

    style = settings.get("text_style", {})

    fontsize = int(style.get("fontsize", 64))
    fontcolor = style.get("fontcolor", "white")

    box = int(style.get("box", 1))
    boxcolor = style.get("boxcolor", "black@0.45")
    boxborderw = int(style.get("boxborderw", 18))

    margin_y = int(style.get("margin_y", 100))

    font = settings.get("font")

    if not font:
        die(
            "settings.font fehlt.\n"
            "Beispiel:\n"
            "\"font\": \"/pfad/zur/font.ttf\""
        )

    font = Path(font)

    line_height = fontsize * 1.25

    block_y = get_text_y(
        text_position,
        len(text_lines),
        fontsize,
        1920,
        margin_y
    )

    current = input_label

    for index, text in enumerate(text_lines):

        if index == len(text_lines) - 1:
            current_output = output_label
        else:
            current_output = f"{output_label}_line_{index}"

        y_expression = (
            f"({block_y})+{index * line_height:.3f}"
        )

        text_escaped = ff_escape(text)
        font_escaped = ff_escape(str(font))

        drawtext = (
            f"drawtext="
            f"fontfile={font_escaped}:"
            f"text={text_escaped}:"
            f"fontcolor={fontcolor}:"
            f"fontsize={fontsize}:"
            f"x=(w-text_w)/2:"
            f"y={y_expression}:"
            f"box={box}:"
            f"boxcolor={boxcolor}:"
            f"boxborderw={boxborderw}"
        )

        filters.append(
            f"{label(current)}{drawtext}{label(current_output)}"
        )

        current = current_output


# ============================================================
# Video / Image
# ============================================================

def add_video_segment(
    filters,
    clip,
    index,
    input_index,
    duration,
    settings
):
    """
    Video als normiertes 1080x1920-Segment.
    """

    width = int(settings.get("width", 1080))
    height = int(settings.get("height", 1920))
    fps = int(settings.get("fps", 30))

    start = parse_time(clip.get("start"))

    end_value = clip.get("end")

    # end = 00:00 => echte Quelle bis zum Ende
    if end_value is not None:
        parsed_end = parse_time(end_value)

        if parsed_end == 0:
            parsed_end = None
    else:
        parsed_end = None

    input_label = f"{input_index}:v"

    base = f"base_{index}"

    chain = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        f"fps={fps}",
        "format=yuv420p",
    ]

    if start is not None and start > 0:
        chain.insert(0, f"trim=start={start:.6f}")

    if parsed_end is not None:
        if start is not None:
            clip_duration = max(parsed_end - start, 0.01)
            chain.append(f"trim=duration={clip_duration:.6f}")
        else:
            clip_duration = max(parsed_end, 0.01)
            chain.append(f"trim=duration={clip_duration:.6f}")
    else:
        # Dauer bereits vorher bestimmt
        if start is not None:
            chain.append(f"trim=duration={duration:.6f}")

    chain.extend([
        "settb=AVTB",
        "setpts=PTS-STARTPTS",
    ])
    filters.append(label(input_label) + ",".join(chain) + label(base))

    text_lines = normalize_text(clip.get("text"))

    add_text_filters(
        filters=filters,
        input_label=base,
        output_label=f"v{index}",
        text_lines=text_lines,
        text_position=clip.get("text_position", "center"),
        settings=settings
    )


def add_image_segment(
    filters,
    clip,
    index,
    input_index,
    duration,
    settings
):
    """
    Bild wird wie ein Video behandelt:
      - auf 1080x1920 gebracht
      - für duration angezeigt
      - mit PTS versehen
      - optional Text
    """

    width = int(settings.get("width", 1080))
    height = int(settings.get("height", 1920))
    fps = int(settings.get("fps", 30))

    base = f"base_{index}"

    chain = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        f"fps={fps}",
        "format=yuv420p",
        f"trim=duration={duration:.6f}",
        "settb=AVTB",
        "setpts=PTS-STARTPTS"
    ]

    filters.append(label(f"{input_index}:v") + ",".join(chain) + label(base))

    text_lines = normalize_text(clip.get("text"))

    add_text_filters(
        filters=filters,
        input_label=base,
        output_label=f"v{index}",
        text_lines=text_lines,
        text_position=clip.get("text_position", "center"),
        settings=settings
    )


# ============================================================
# Audio
# ============================================================

def add_video_audio(
    filters,
    clip,
    index,
    input_index,
    duration,
    has_audio,
    settings
):
    """
    Audio eines Videos.

    Falls kein Audio vorhanden:
      anullsrc

    Damit funktioniert der Clip trotzdem.
    """

    source_volume = float(
        clip.get(
            "source_volume",
            settings.get("source_volume", 1.0)
        )
    )

    out = f"src_a{index}"

    if has_audio:
        chain = [
            "aresample=48000",
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo",
            f"volume={source_volume}",
            f"atrim=duration={duration:.6f}",
            "asetpts=PTS-STARTPTS",
        ]
        filters.append(label(f"{input_index}:a") + ",".join(chain) + label(out))

    else:
        filters.append(
            f"anullsrc="
            f"channel_layout=stereo:"
            f"sample_rate=48000,"
            f"atrim=duration={duration:.6f},"
            f"asetpts=PTS-STARTPTS"
            f"{label(out)}"
        )


def add_silent_audio(
    filters,
    index,
    duration
):
    out = f"src_a{index}"

    filters.append(
        f"anullsrc="
        f"channel_layout=stereo:"
        f"sample_rate=48000,"
        f"atrim=duration={duration:.6f},"
        f"asetpts=PTS-STARTPTS"
        f"{label(out)}"
    )


# ============================================================
# Durations
# ============================================================

def determine_clip_duration(clip, media_info):
    clip_type = clip.get("type", "video").lower()

    if clip_type == "image":
        duration = clip.get("duration")

        if duration is None:
            die(
                f"Bild-Element benötigt 'duration':\n{clip}"
            )

        duration = float(duration)

        if duration <= 0:
            die(
                f"Bild-duration muss > 0 sein:\n{clip}"
            )

        return duration

    # Video
    source_duration = media_info["duration"]

    start = parse_time(clip.get("start"))

    if start is None:
        start = 0.0

    end_value = clip.get("end")

    if end_value is None:
        end = source_duration

    else:
        parsed_end = parse_time(end_value)

        # 00:00 = Ende der Quelle
        if parsed_end == 0:
            end = source_duration
        else:
            end = parsed_end

    duration = end - start

    if duration <= 0:
        die(
            f"Video hat eine ungültige Dauer:\n"
            f"{clip}\n"
            f"Quelle: {source_duration:.3f}s"
        )

    return min(duration, max(source_duration - start, 0.01))


# ============================================================
# Music
# ============================================================

def add_music(
    filters,
    clip,
    index,
    input_index,
    total_duration,
    settings
):
    """
    Musik für ein einzelnes Element.

    clip['music'] überschreibt globale Musik für diesen Clip.
    """

    music = clip.get("music")

    if not music:
        return None

    music_volume = float(
        clip.get(
            "music_volume",
            settings.get("music_volume", 0.18)
        )
    )

    music_start = parse_time(
        clip.get(
            "music_start",
            settings.get("music_start", "00:00")
        )
    ) or 0.0

    out = f"element_music_{index}"

    filters.append(
        f"{label(f'{input_index}:a')}"
        f"aresample=48000,"
        f"aformat=sample_fmts=fltp:"
        f"sample_rates=48000:"
        f"channel_layouts=stereo,"
        f"atrim=start={music_start:.6f},"
        f"asetpts=PTS-STARTPTS,"
        f"volume={music_volume},"
        f"atrim=duration={total_duration:.6f},"
        f"asetpts=PTS-STARTPTS"
        f"{label(out)}"
    )

    return out


def add_global_music(
    filters,
    music_input_index,
    music_start,
    music_volume,
    total_duration
):
    """
    Globale Musik wird am Anfang abgeschnitten und
    anschließend auf die Reel-Länge begrenzt.

    Falls das Musikstück kürzer ist als das Reel,
    wird es geloopt.
    """

    out = "global_music_a"

    start = parse_time(music_start) or 0.0

    filters.append(
        f"{label(f'{music_input_index}:a')}"
        f"aresample=48000,"
        f"aformat=sample_fmts=fltp:"
        f"sample_rates=48000:"
        f"channel_layouts=stereo,"
        f"atrim=start={start:.6f},"
        f"asetpts=PTS-STARTPTS,"
        f"volume={music_volume},"
        f"aloop=loop=-1:size=2147483647,"
        f"atrim=duration={total_duration:.6f},"
        f"asetpts=PTS-STARTPTS"
        f"{label(out)}"
    )

    return out


# ============================================================
# Main compiler
# ============================================================

def main():
    require_program("ffmpeg")
    require_program("ffprobe")

    if len(sys.argv) < 2:
        print(
            "Verwendung:\n"
            "  python3 reel_compiler.py config.json"
        )
        sys.exit(1)

    config_path = Path(sys.argv[1]).resolve()

    if not config_path.exists():
        die(f"Config nicht gefunden: {config_path}")

    with open(
        config_path,
        "r",
        encoding="utf-8-sig"
    ) as f:
        config = json.load(f)

    base_dir = config_path.parent

    settings = config.get("settings", {})
    clips = config.get("clips", [])

    if not clips:
        die("Keine clips in der Config.")

    width = int(settings.get("width", 1080))
    height = int(settings.get("height", 1920))
    fps = int(settings.get("fps", 30))

    transition = settings.get(
        "transition",
        "fade"
    )

    transition_duration = float(
        settings.get(
            "transition_duration",
            0.25
        )
    )

    preset = settings.get(
        "preset",
        "medium"
    )

    crf = int(
        settings.get(
            "crf",
            20
        )
    )

    audio_bitrate = settings.get(
        "audio_bitrate",
        "192k"
    )

    # --------------------------------------------------------
    # Font
    # --------------------------------------------------------

    font = settings.get("font")

    if not font:
        die(
            "settings.font fehlt."
        )

    font_path = resolve_path(
        base_dir,
        font
    )

    if not font_path.exists():
        die(
            f"Font nicht gefunden:\n"
            f"{font_path}"
        )

    settings["font"] = str(font_path)

    # --------------------------------------------------------
    # Resolve clips
    # --------------------------------------------------------

    resolved_clips = []

    total_duration = 0.0

    for index, clip in enumerate(clips):

        clip_type = clip.get(
            "type",
            "video"
        ).lower()

        if clip_type not in ("video", "image"):
            die(
                f"Clip {index}: unbekannter type '{clip_type}'. "
                f"Erlaubt: video, image"
            )

        filename = clip.get("file")

        if not filename:
            die(
                f"Clip {index}: 'file' fehlt."
            )

        path = resolve_path(
            base_dir,
            filename
        )

        if not path.exists():
            die(
                f"Datei nicht gefunden:\n"
                f"{path}"
            )

        info = get_media_info(path)

        if clip_type == "video":

            if not info["has_video"]:
                die(
                    f"Datei enthält keinen Videostream:\n"
                    f"{path}"
                )

        duration = determine_clip_duration(
            clip,
            info
        )

        resolved_clips.append({
            "clip": clip,
            "type": clip_type,
            "path": path,
            "info": info,
            "duration": duration,
        })

        total_duration += duration

    print(
        f"\nReel-Dauer: {total_duration:.3f} Sekunden"
    )

    # --------------------------------------------------------
    # Inputs
    # --------------------------------------------------------

    inputs = []
    input_meta = []

    for item in resolved_clips:

        clip_type = item["type"]
        path = item["path"]
        duration = item["duration"]

        if clip_type == "image":

            inputs.extend([
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-t",
                f"{duration:.6f}",
                "-i",
                str(path)
            ])

        else:

            # Videos normal einlesen
            inputs.extend([
                "-i",
                str(path)
            ])

        input_meta.append({
            "input_index": len(input_meta),
            **item
        })

    # --------------------------------------------------------
    # Global music
    # --------------------------------------------------------

    global_music = settings.get("music")

    global_music_input_index = None

    if global_music:
        global_music_path = resolve_path(
            base_dir,
            global_music
        )

        if not global_music_path.exists():
            die(
                f"Globale Musik nicht gefunden:\n"
                f"{global_music_path}"
            )

        global_music_input_index = len(input_meta)

        inputs.extend([
            "-stream_loop",
            "-1",
            "-i",
            str(global_music_path)
        ])

    # --------------------------------------------------------
    # Per-element music
    #
    # Wichtig:
    # Musikdateien werden nur dann als Input hinzugefügt,
    # wenn ein Clip explizit 'music' besitzt.
    # --------------------------------------------------------

    element_music_inputs = {}

    for index, item in enumerate(resolved_clips):

        music = item["clip"].get("music")

        if not music:
            continue

        music_path = resolve_path(
            base_dir,
            music
        )

        if not music_path.exists():
            die(
                f"Musik für Clip {index} nicht gefunden:\n"
                f"{music_path}"
            )

        music_input_index = len(input_meta)

        inputs.extend([
            "-stream_loop",
            "-1",
            "-i",
            str(music_path)
        ])

        element_music_inputs[index] = music_input_index

    # --------------------------------------------------------
    # Filter graph
    # --------------------------------------------------------

    filters = []

    # --------------------------------------------------------
    # Video + Audio pro Clip
    # --------------------------------------------------------

    for index, item in enumerate(resolved_clips):

        clip = item["clip"]
        clip_type = item["type"]
        duration = item["duration"]

        #input_index = item["input_index"]
        input_index = index

        if clip_type == "video":

            add_video_segment(
                filters=filters,
                clip=clip,
                index=index,
                input_index=input_index,
                duration=duration,
                settings=settings
            )

            add_video_audio(
                filters=filters,
                clip=clip,
                index=index,
                input_index=input_index,
                duration=duration,
                has_audio=item["info"]["has_audio"],
                settings=settings
            )

        else:

            add_image_segment(
                filters=filters,
                clip=clip,
                index=index,
                input_index=input_index,
                duration=duration,
                settings=settings
            )

            add_silent_audio(
                filters=filters,
                index=index,
                duration=duration
            )

    # --------------------------------------------------------
    # Video transitions
    # --------------------------------------------------------

    if len(resolved_clips) == 1:

        final_video_label = "v0"

    else:

        current_video = "v0"
        accumulated_duration = resolved_clips[0]["duration"]

        for index in range(1, len(resolved_clips)):

            current_duration = resolved_clips[index]["duration"]

            transition_duration_actual = min(
                transition_duration,
                resolved_clips[index - 1]["duration"] / 2,
                current_duration / 2
            )

            if transition_duration_actual <= 0:
                transition_duration_actual = 0.01

            offset = (
                accumulated_duration
                - transition_duration_actual
            )

            next_video = f"v{index}"
            output = f"video_transition_{index}"

            filters.append(
                f"{label(current_video)}"
                f"{label(next_video)}"
                f"xfade="
                f"transition={ff_escape(transition)}:"
                f"duration={transition_duration_actual:.6f}:"
                f"offset={offset:.6f}"
                f"{label(output)}"
            )

            current_video = output

            accumulated_duration += (
                current_duration
                - transition_duration_actual
            )

        final_video_label = current_video

    # --------------------------------------------------------
    # Audio transitions
    # --------------------------------------------------------

    if len(resolved_clips) == 1:

        final_audio_label = "src_a0"

    else:

        current_audio = "src_a0"

        for index in range(1, len(resolved_clips)):

            prev_duration = resolved_clips[index - 1]["duration"]
            current_duration = resolved_clips[index]["duration"]

            audio_transition_duration = min(
                transition_duration,
                prev_duration / 2,
                current_duration / 2
            )

            if audio_transition_duration <= 0:
                audio_transition_duration = 0.01

            output = f"audio_transition_{index}"

            filters.append(
                f"{label(current_audio)}"
                f"{label(f'src_a{index}')}"
                f"acrossfade="
                f"d={audio_transition_duration:.6f}:"
                f"c1=tri:"
                f"c2=tri"
                f"{label(output)}"
            )

            current_audio = output

        final_audio_label = current_audio

    # --------------------------------------------------------
    # Music
    # --------------------------------------------------------

    music_labels = []

    # Global music
    #
    # Globale Musik wird nur dort verwendet, wo kein
    # clip-spezifisches 'music' definiert wurde.
    #
    # Wenn ein Clip 'music' besitzt, überschreibt diese
    # Musik die globale Musik für diesen Clip.
    #
    # Dafür erzeugen wir bei globaler Musik zunächst
    # einen vollständigen Musikstream und verwenden ihn
    # anschließend als Hintergrund.
    #
    # Die explizite Clip-Musik wird zusätzlich gemischt.
    # --------------------------------------------------------

    if global_music_input_index is not None:

        global_music_start = settings.get(
            "music_start",
            "00:00"
        )

        global_music_volume = float(
            settings.get(
                "music_volume",
                0.18
            )
        )

        global_music_label = add_global_music(
            filters=filters,
            music_input_index=global_music_input_index,
            music_start=global_music_start,
            music_volume=global_music_volume,
            total_duration=total_duration
        )

        music_labels.append(
            global_music_label
        )

    # Clip-spezifische Musik
    for index, music_input_index in element_music_inputs.items():

        clip_music = add_music(
            filters=filters,
            clip=resolved_clips[index]["clip"],
            index=index,
            input_index=music_input_index,
            total_duration=resolved_clips[index]["duration"],
            settings=settings
        )

        if clip_music:
            music_labels.append(
                clip_music
            )

    # --------------------------------------------------------
    # Final Audio
    # --------------------------------------------------------

    if music_labels:

        # Normale globale Musik:
        #
        # Falls einzelne Clips eigene Musik besitzen,
        # wird diese zusätzlich gemischt. Das bedeutet:
        #
        # global music = Hintergrund
        # clip music   = zusätzliche Musik
        #
        # Für eine echte harte "Override"-Funktion kann
        # später eine Ducking-/Timeline-Mischung ergänzt werden.

        mix_inputs = [final_audio_label] + music_labels

        mix_count = len(mix_inputs)

        filters.append(
            "".join(label(x) for x in mix_inputs)
            +
            f"amix="
            f"inputs={mix_count}:"
            f"duration=first:"
            f"dropout_transition=0:"
            f"normalize=0"
            f"{label('final_audio')}"
        )

        final_audio_label = "final_audio"

    else:

        final_audio_label = final_audio_label

    # --------------------------------------------------------
    # Filter graph
    # --------------------------------------------------------

    filter_complex = ";".join(filters)

    print("\n========== FILTER GRAPH ==========\n")
    print(filter_complex)
    print("\n===================================\n")

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    output_value = config.get(
        "output",
        "reel.mp4"
    )

    output_path = resolve_path(
        base_dir,
        output_value
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # FFmpeg command
    # --------------------------------------------------------

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,

        "-filter_complex",
        filter_complex,

        "-map",
        label(final_video_label),

        "-map",
        label(final_audio_label),

        "-c:v",
        "libx264",

        "-preset",
        preset,

        "-crf",
        str(crf),

        "-pix_fmt",
        "yuv420p",

        "-r",
        str(fps),

        "-c:a",
        "aac",

        "-b:a",
        audio_bitrate,

        "-movflags",
        "+faststart",

        "-t",
        f"{total_duration:.6f}",

        str(output_path)
    ]

    run(cmd)

    print(
        "\n========================================"
    )
    print("REEL ERFOLGREICH ERSTELLT")
    print(
        f"Output: {output_path}"
    )
    print(
        f"Dauer : {total_duration:.2f}s"
    )
    print(
        f"Format: {width}x{height} @ {fps}fps"
    )
    print(
        "========================================"
    )


if __name__ == "__main__":
    main()
