#!/usr/bin/env python3
"""
Smart Clip Exporter

Erzeugt aus einem Rohvideo einen einzelnen Social-Media-Clip.

Funktionen:
- frei wählbarer Start / Ende
- frei wählbare Zielauflösung
- center crop
- intelligenter Hund-Crop
- mehrere Hunde werden gemeinsam berücksichtigt
- weiches Kamera-Following
- automatische Sicherheitsränder
- automatisches Herauszoomen
- temporäre Fehldetektionen werden abgefangen
- komplett lokale Verarbeitung nach Installation des Modells
- Ausgabe via FFmpeg

Beispiel:

    python smart_clip_exporter.py clip.json
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


# ============================================================
# Datenstrukturen
# ============================================================

@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float

    @property
    def width(self):
        return self.x2 - self.x1

    @property
    def height(self):
        return self.y2 - self.y1

    @property
    def center_x(self):
        return (self.x1 + self.x2) / 2

    @property
    def center_y(self):
        return (self.y1 + self.y2) / 2


@dataclass
class CameraPoint:
    time: float
    center_x: float
    center_y: float
    crop_width: float
    crop_height: float


# ============================================================
# Allgemeine Hilfsfunktionen
# ============================================================

def die(message: str):
    print(f"\nFEHLER: {message}", file=sys.stderr)
    sys.exit(1)


def check_ffmpeg():
    if shutil.which("ffmpeg") is None:
        die(
            "FFmpeg wurde nicht gefunden. "
            "Bitte installieren und in PATH aufnehmen."
        )


def parse_time(value) -> float:
    """
    Akzeptiert:
        12.5
        "00:12"
        "01:15.5"
        "00:01:15.5"
    """

    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()

    if ":" not in value:
        return float(value)

    parts = value.split(":")

    if len(parts) == 2:
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds

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


def parse_resolution(value):
    if isinstance(value, str):

        if "x" not in value.lower():
            raise ValueError(
                "Auflösung muss z.B. 1080x1920 sein."
            )

        w, h = value.lower().split("x", 1)

        return int(w), int(h)

    if isinstance(value, list) and len(value) == 2:
        return int(value[0]), int(value[1])

    raise ValueError(
        "Ungültige Auflösung."
    )


def clamp(value, minimum, maximum):
    return max(
        minimum,
        min(maximum, value)
    )


def lerp(a, b, amount):
    return a + (b - a) * amount


# ============================================================
# YOLO
# ============================================================

class DogDetector:

    DOG_CLASS = 16

    def __init__(
        self,
        model_path: str,
        confidence: float,
    ):

        print(
            f"Lade YOLO-Modell: {model_path}"
        )

        self.model = YOLO(model_path)
        self.confidence = confidence

    def detect(
        self,
        frame,
    ) -> list[Detection]:

        results = self.model.predict(
            frame,
            classes=[self.DOG_CLASS],
            conf=self.confidence,
            verbose=False,
        )

        if not results:
            return []

        result = results[0]

        if result.boxes is None:
            return []

        detections = []

        for box, conf in zip(
            result.boxes.xyxy,
            result.boxes.conf,
        ):

            coords = (
                box
                .cpu()
                .numpy()
            )

            x1, y1, x2, y2 = coords

            detections.append(
                Detection(
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                    confidence=float(conf),
                )
            )

        return detections


# ============================================================
# Bounding Box für einen oder mehrere Hunde
# ============================================================

def combine_detections(
    detections: list[Detection],
    frame_width: int,
    frame_height: int,
    multi_dog_mode: str = "all",
):
    """
    Erzeugt eine gemeinsame Bounding Box.

    multi_dog_mode:
        all
            alle erkannten Hunde berücksichtigen

        largest
            nur den größten Hund berücksichtigen

        strongest
            nur den Hund mit höchster confidence
    """

    if not detections:
        return None

    if multi_dog_mode == "largest":

        detection = max(
            detections,
            key=lambda d: d.width * d.height,
        )

        return detection

    if multi_dog_mode == "strongest":

        return max(
            detections,
            key=lambda d: d.confidence,
        )

    # Standard:
    # gemeinsame Box aller Hunde.

    x1 = min(
        d.x1 for d in detections
    )

    y1 = min(
        d.y1 for d in detections
    )

    x2 = max(
        d.x2 for d in detections
    )

    y2 = max(
        d.y2 for d in detections
    )

    return Detection(
        x1=clamp(x1, 0, frame_width),
        y1=clamp(y1, 0, frame_height),
        x2=clamp(x2, 0, frame_width),
        y2=clamp(y2, 0, frame_height),
        confidence=max(
            d.confidence
            for d in detections
        ),
    )


# ============================================================
# Crop-Geometrie
# ============================================================

def calculate_base_crop(
    frame_width,
    frame_height,
    target_width,
    target_height,
):
    """
    Ermittelt die größtmögliche Crop-Fläche mit dem
    gewünschten Seitenverhältnis.
    """

    target_ratio = (
        target_width / target_height
    )

    source_ratio = (
        frame_width / frame_height
    )

    if source_ratio > target_ratio:

        crop_height = frame_height
        crop_width = (
            crop_height
            * target_ratio
        )

    else:

        crop_width = frame_width
        crop_height = (
            crop_width
            / target_ratio
        )

    return (
        crop_width,
        crop_height,
    )


def calculate_smart_camera(
    detection: Detection,
    frame_width,
    frame_height,
    base_crop_width,
    base_crop_height,
    target_width,
    target_height,
    zoom,
    margin,
    min_zoom,
    max_zoom,
):
    """
    Berechnet eine Kameraeinstellung.

    Ziel:
        Hund vollständig sichtbar halten.

    Dabei wird automatisch herausgezoomt,
    wenn der Hund zu groß für den aktuellen
    Ausschnitt ist.
    """

    dog_width = detection.width
    dog_height = detection.height

    # Sicherheitsrand.
    required_width = (
        dog_width
        * (1 + margin * 2)
    )

    required_height = (
        dog_height
        * (1 + margin * 2)
    )

    # Wie viel Zoom ist möglich?
    #
    # Zoom 1.0 = maximaler Ausschnitt
    # Zoom > 1 = näher ran

    width_zoom = (
        base_crop_width
        / required_width
    )

    height_zoom = (
        base_crop_height
        / required_height
    )

    desired_zoom = min(
        width_zoom,
        height_zoom,
    )

    desired_zoom *= zoom

    desired_zoom = clamp(
        desired_zoom,
        min_zoom,
        max_zoom,
    )

    crop_width = (
        base_crop_width
        / desired_zoom
    )

    crop_height = (
        base_crop_height
        / desired_zoom
    )

    return (
        detection.center_x,
        detection.center_y,
        crop_width,
        crop_height,
    )


# ============================================================
# Kamera-Plan
# ============================================================

def create_camera_plan(
    input_file,
    start,
    end,
    target_width,
    target_height,
    model_path,
    confidence,
    detection_interval,
    smoothing,
    margin,
    zoom,
    min_zoom,
    max_zoom,
    multi_dog_mode,
):

    cap = cv2.VideoCapture(
        str(input_file)
    )

    if not cap.isOpened():
        die(
            f"Video konnte nicht geöffnet werden: "
            f"{input_file}"
        )

    fps = (
        cap.get(
            cv2.CAP_PROP_FPS
        )
        or 30
    )

    frame_width = int(
        cap.get(
            cv2.CAP_PROP_FRAME_WIDTH
        )
    )

    frame_height = int(
        cap.get(
            cv2.CAP_PROP_FRAME_HEIGHT
        )
    )

    if frame_width <= 0:
        die(
            "Ungültige Video-Breite."
        )

    if frame_height <= 0:
        die(
            "Ungültige Video-Höhe."
        )

    base_crop_width, base_crop_height = (
        calculate_base_crop(
            frame_width,
            frame_height,
            target_width,
            target_height,
        )
    )

    detector = DogDetector(
        model_path=model_path,
        confidence=confidence,
    )

    print()
    print(
        f"Quelle: {frame_width}x{frame_height}"
    )

    print(
        f"Ziel:   {target_width}x{target_height}"
    )

    print(
        f"Zeitraum: {start:.2f}s – {end:.2f}s"
    )

    print()
    print(
        "Starte lokale Hund-Erkennung..."
    )

    positions = []

    current_time = start

    last_detection = None
    lost_detections = 0

    # Maximal so lange darf der Hund verschwinden,
    # bevor wir die Kamera langsam wieder in die
    # Mitte bewegen.
    max_lost_time = 1.5

    smoothed_x = frame_width / 2
    smoothed_y = frame_height / 2

    smoothed_crop_width = (
        base_crop_width
    )

    smoothed_crop_height = (
        base_crop_height
    )

    sample_number = 0

    while current_time < end:

        cap.set(
            cv2.CAP_PROP_POS_MSEC,
            current_time * 1000,
        )

        success, frame = cap.read()

        if not success:
            break

        detections = detector.detect(
            frame
        )

        combined = combine_detections(
            detections,
            frame_width,
            frame_height,
            multi_dog_mode,
        )

        if combined is not None:

            (
                desired_x,
                desired_y,
                desired_crop_width,
                desired_crop_height,
            ) = calculate_smart_camera(
                detection=combined,
                frame_width=frame_width,
                frame_height=frame_height,
                base_crop_width=base_crop_width,
                base_crop_height=base_crop_height,
                target_width=target_width,
                target_height=target_height,
                zoom=zoom,
                margin=margin,
                min_zoom=min_zoom,
                max_zoom=max_zoom,
            )

            last_detection = (
                desired_x,
                desired_y,
                desired_crop_width,
                desired_crop_height,
            )

            lost_detections = 0

        else:

            lost_detections += 1

            lost_time = (
                lost_detections
                * detection_interval
            )

            if (
                last_detection is not None
                and lost_time < max_lost_time
            ):

                (
                    desired_x,
                    desired_y,
                    desired_crop_width,
                    desired_crop_height,
                ) = last_detection

            else:

                # Wenn der Hund wirklich weg ist,
                # langsam Richtung Bildmitte fahren.

                desired_x = (
                    frame_width / 2
                )

                desired_y = (
                    frame_height / 2
                )

                desired_crop_width = (
                    base_crop_width
                )

                desired_crop_height = (
                    base_crop_height
                )

        # --------------------------------------------
        # Zeitabhängige Glättung
        # --------------------------------------------

        smoothed_x = lerp(
            smoothed_x,
            desired_x,
            smoothing,
        )

        smoothed_y = lerp(
            smoothed_y,
            desired_y,
            smoothing,
        )

        smoothed_crop_width = lerp(
            smoothed_crop_width,
            desired_crop_width,
            smoothing,
        )

        smoothed_crop_height = lerp(
            smoothed_crop_height,
            desired_crop_height,
            smoothing,
        )

        positions.append(
            CameraPoint(
                time=current_time - start,
                center_x=smoothed_x,
                center_y=smoothed_y,
                crop_width=smoothed_crop_width,
                crop_height=smoothed_crop_height,
            )
        )

        sample_number += 1

        elapsed = (
            current_time - start
        )

        duration = end - start

        progress = (
            elapsed / duration
            if duration > 0
            else 1
        )

        print(
            f"\rAnalyse: "
            f"{progress * 100:5.1f}% "
            f"| Hunde: {len(detections):2d}",
            end="",
        )

        current_time += detection_interval

    print()

    cap.release()

    if not positions:
        die(
            "Keine Kamera-Positionen erzeugt."
        )

    return positions, fps


# ============================================================
# Kamera-Plan speichern
# ============================================================

def save_camera_plan(
    positions,
    filename,
):

    data = []

    for p in positions:

        data.append(
            {
                "time": p.time,
                "center_x": p.center_x,
                "center_y": p.center_y,
                "crop_width": p.crop_width,
                "crop_height": p.crop_height,
            }
        )

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            indent=2,
        )


# ============================================================
# Crop-Video erzeugen
# ============================================================

def render_with_opencv(
    input_file,
    output_file,
    start,
    end,
    positions,
    target_width,
    target_height,
    fps,
):
    """
    Rendert den eigentlichen Crop direkt mit OpenCV.

    Vorteil:
        Wir können für jeden Frame eine präzise
        Kamera-Interpolation verwenden.

    Audio wird anschließend per FFmpeg aus dem
    Originalvideo übernommen.
    """

    cap = cv2.VideoCapture(
        str(input_file)
    )

    if not cap.isOpened():
        die(
            "Video konnte zum Rendern nicht geöffnet werden."
        )

    source_width = int(
        cap.get(
            cv2.CAP_PROP_FRAME_WIDTH
        )
    )

    source_height = int(
        cap.get(
            cv2.CAP_PROP_FRAME_HEIGHT
        )
    )

    fourcc = cv2.VideoWriter_fourcc(
        *"mp4v"
    )

    writer = cv2.VideoWriter(
        str(output_file),
        fourcc,
        fps,
        (
            target_width,
            target_height,
        ),
    )

    if not writer.isOpened():
        die(
            "OpenCV konnte die temporäre Videodatei nicht öffnen."
        )

    def interpolate(t):

        if t <= positions[0].time:
            return positions[0]

        if t >= positions[-1].time:
            return positions[-1]

        # Binäre Suche wäre bei langen Videos effizienter.
        # Für kurze Clips reicht eine lineare Suche.
        for i in range(
            len(positions) - 1
        ):

            a = positions[i]
            b = positions[i + 1]

            if (
                a.time
                <= t
                <= b.time
            ):

                if b.time == a.time:
                    amount = 0
                else:
                    amount = (
                        t - a.time
                    ) / (
                        b.time - a.time
                    )

                return CameraPoint(
                    time=t,

                    center_x=lerp(
                        a.center_x,
                        b.center_x,
                        amount,
                    ),

                    center_y=lerp(
                        a.center_y,
                        b.center_y,
                        amount,
                    ),

                    crop_width=lerp(
                        a.crop_width,
                        b.crop_width,
                        amount,
                    ),

                    crop_height=lerp(
                        a.crop_height,
                        b.crop_height,
                        amount,
                    ),
                )

        return positions[-1]

    cap.set(
        cv2.CAP_PROP_POS_MSEC,
        start * 1000,
    )

    frame_index = 0

    total_frames = int(
        (end - start) * fps
    )

    while True:

        success, frame = cap.read()

        if not success:
            break

        current_time = (
            start
            + frame_index / fps
        )

        if current_time >= end:
            break

        relative_time = (
            current_time - start
        )

        camera = interpolate(
            relative_time
        )

        crop_width = int(
            camera.crop_width
        )

        crop_height = int(
            camera.crop_height
        )

        # Sicherheitsprüfung.
        crop_width = int(
            clamp(
                crop_width,
                2,
                source_width,
            )
        )

        crop_height = int(
            clamp(
                crop_height,
                2,
                source_height,
            )
        )

        x = int(
            camera.center_x
            - crop_width / 2
        )

        y = int(
            camera.center_y
            - crop_height / 2
        )

        x = int(
            clamp(
                x,
                0,
                source_width
                - crop_width,
            )
        )

        y = int(
            clamp(
                y,
                0,
                source_height
                - crop_height,
            )
        )

        cropped = frame[
            y:y + crop_height,
            x:x + crop_width,
        ]

        resized = cv2.resize(
            cropped,
            (
                target_width,
                target_height,
            ),
            interpolation=cv2.INTER_LANCZOS4,
        )

        writer.write(
            resized
        )

        frame_index += 1

        if total_frames > 0:

            progress = (
                frame_index
                / total_frames
            )

            print(
                f"\rRender: "
                f"{progress * 100:5.1f}%",
                end="",
            )

    print()

    cap.release()
    writer.release()


# ============================================================
# Audio von Original übernehmen
# ============================================================

def add_original_audio(
    silent_video,
    source_video,
    output,
    start,
    duration,
):

    cmd = [
        "ffmpeg",
        "-y",

        "-i",
        str(silent_video),

        "-ss",
        str(start),

        "-i",
        str(source_video),

        "-t",
        str(duration),

        "-map",
        "0:v:0",

        "-map",
        "1:a?",

        "-c:v",
        "libx264",

        "-crf",
        "18",

        "-preset",
        "medium",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-shortest",

        "-movflags",
        "+faststart",

        str(output),
    ]

    print(
        "\nÜbernehme Originalton..."
    )

    result = subprocess.run(
        cmd
    )

    if result.returncode != 0:
        die(
            "Audio-Export fehlgeschlagen."
        )


# ============================================================
# Center Crop
# ============================================================

def render_center_crop(
    input_file,
    output_file,
    start,
    end,
    width,
    height,
):

    duration = (
        end - start
    )

    filter_expression = (
        f"scale={width}:{height}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"setsar=1"
    )

    cmd = [
        "ffmpeg",
        "-y",

        "-ss",
        str(start),

        "-i",
        str(input_file),

        "-t",
        str(duration),

        "-vf",
        filter_expression,

        "-r",
        "30",

        "-c:v",
        "libx264",

        "-crf",
        "18",

        "-preset",
        "medium",

        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-movflags",
        "+faststart",

        str(output_file),
    ]

    result = subprocess.run(
        cmd
    )

    if result.returncode != 0:
        die(
            "Center-Crop fehlgeschlagen."
        )


# ============================================================
# Hauptprogramm
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Lokaler Smart Clip Exporter "
            "mit automatischer Hundeverfolgung."
        )
    )

    parser.add_argument(
        "config",
        help="JSON-Konfiguration"
    )

    args = parser.parse_args()

    check_ffmpeg()

    config_path = (
        Path(args.config)
        .resolve()
    )

    if not config_path.exists():
        die(
            f"Konfiguration nicht gefunden: "
            f"{config_path}"
        )

    with open(
        config_path,
        "r",
        encoding="utf-8",
    ) as f:

        config = json.load(f)

    base = config_path.parent

    generateClip = config.get("generateClip", False)
    if not generateClip:
        die(
            f"Do Not Generate"
        )


    # --------------------------------------------------------
    # Eingaben
    # --------------------------------------------------------

    input_file = Path(
        config["input"]
    )

    if not input_file.is_absolute():
        input_file = (
            base / input_file
        )

    output_file = Path(
        config.get(
            "output",
            "clip_output.mp4",
        )
    )

    if not output_file.is_absolute():
        output_file = (
            base / output_file
        )

    start = parse_time(
        config.get(
            "start",
            0,
        )
    )

    end = parse_time(
        config["end"]
    )

    if end <= start:
        die(
            "end muss größer als start sein."
        )

    width, height = parse_resolution(
        config.get(
            "resolution",
            "1080x1920",
        )
    )

    crop_mode = config.get(
        "crop",
        "dog",
    ).lower()

    # --------------------------------------------------------
    # Smart-Crop Einstellungen
    # --------------------------------------------------------

    model = config.get(
        "model",
        "yolo11n.pt",
    )

    confidence = float(
        config.get(
            "confidence",
            0.35,
        )
    )

    detection_interval = float(
        config.get(
            "detection_interval",
            0.20,
        )
    )

    smoothing = float(
        config.get(
            "smoothing",
            0.18,
        )
    )

    margin = float(
        config.get(
            "margin",
            0.20,
        )
    )

    zoom = float(
        config.get(
            "zoom",
            1.05,
        )
    )

    min_zoom = float(
        config.get(
            "min_zoom",
            0.85,
        )
    )

    max_zoom = float(
        config.get(
            "max_zoom",
            1.35,
        )
    )

    multi_dog_mode = config.get(
        "multi_dog_mode",
        "all",
    )

    # --------------------------------------------------------
    # Prüfung
    # --------------------------------------------------------

    if not input_file.exists():
        die(
            f"Rohvideo nicht gefunden: "
            f"{input_file}"
        )

    duration = (
        end - start
    )

    print()
    print(
        "=========================================="
    )

    print(
        " SMART CLIP EXPORTER"
    )

    print(
        "=========================================="
    )

    print(
        f"Quelle:       {input_file}"
    )

    print(
        f"Ausgabe:      {output_file}"
    )

    print(
        f"Zeitraum:     {start:.2f}s – {end:.2f}s"
    )

    print(
        f"Auflösung:    {width}x{height}"
    )

    print(
        f"Crop:         {crop_mode}"
    )

    print(
        "=========================================="
    )

    # --------------------------------------------------------
    # Center Crop
    # --------------------------------------------------------

    if crop_mode == "center":

        render_center_crop(
            input_file=input_file,
            output_file=output_file,
            start=start,
            end=end,
            width=width,
            height=height,
        )

        print()
        print(
            f"FERTIG: {output_file}"
        )

        return

    # --------------------------------------------------------
    # Smart Dog Crop
    # --------------------------------------------------------

    if crop_mode != "dog":
        die(
            "crop muss 'dog' oder 'center' sein."
        )

    positions, fps = (
        create_camera_plan(
            input_file=input_file,
            start=start,
            end=end,
            target_width=width,
            target_height=height,
            model_path=model,
            confidence=confidence,
            detection_interval=detection_interval,
            smoothing=smoothing,
            margin=margin,
            zoom=zoom,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            multi_dog_mode=multi_dog_mode,
        )
    )

    # --------------------------------------------------------
    # Temporäre Dateien
    # --------------------------------------------------------

    with tempfile.TemporaryDirectory(
        prefix="smart_clip_"
    ) as temp_dir:

        temp_dir = Path(
            temp_dir
        )

        silent_video = (
            temp_dir
            / "video_only.mp4"
        )

        # Optional:
        # Kamera-Plan speichern, falls man ihn später
        # analysieren/debuggen möchte.

        if config.get(
            "save_camera_plan",
            False,
        ):

            plan_file = (
                output_file.parent
                / (
                    output_file.stem
                    + "_camera_plan.json"
                )
            )

            save_camera_plan(
                positions,
                plan_file,
            )

            print(
                f"Kamera-Plan gespeichert: "
                f"{plan_file}"
            )

        # ----------------------------------------------------
        # Video rendern
        # ----------------------------------------------------

        render_with_opencv(
            input_file=input_file,
            output_file=silent_video,
            start=start,
            end=end,
            positions=positions,
            target_width=width,
            target_height=height,
            fps=30,
        )

        # ----------------------------------------------------
        # Audio zurückführen
        # ----------------------------------------------------

        add_original_audio(
            silent_video=silent_video,
            source_video=input_file,
            output=output_file,
            start=start,
            duration=duration,
        )

    print()
    print(
        "=========================================="
    )

    print(
        f"FERTIG: {output_file}"
    )

    print(
        "=========================================="
    )


if __name__ == "__main__":
    main()
