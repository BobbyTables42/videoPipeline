#!/usr/bin/env python3

import json
import sys


def main():
    if len(sys.argv) != 2:
        print(f"Verwendung: {sys.argv[0]} DATEI.json")
        sys.exit(1)

    filename = sys.argv[1]

    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Datei nicht gefunden: {filename}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Ungültige JSON-Datei: {e}")
        sys.exit(1)

    clips = data.get("clips", [])

    texts = []

    for clip in clips:
        text = clip.get("text")

        if text:
            texts.append("0:00:.500,0:00:.500")
            texts.append(" ".join(text))
            texts.append(" ")

    print("\n".join(texts))


if __name__ == "__main__":
    main()
