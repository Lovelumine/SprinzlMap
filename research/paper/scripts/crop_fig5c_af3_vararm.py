#!/usr/bin/env python3
"""Apply one transparent-content crop to both Figure 5C AF3 renders."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    labels = ("V19", "V14")
    images = {label: Image.open(args.input_dir / f"{label}_raw.png") for label in labels}
    boxes = [image.getchannel("A").getbbox() for image in images.values()]
    width, height = next(iter(images.values())).size
    crop = (
        max(0, min(box[0] for box in boxes) - 44),
        max(0, min(box[1] for box in boxes) - 40),
        min(width, max(box[2] for box in boxes) + 44),
        min(height, max(box[3] for box in boxes) + 40),
    )

    for label, image in images.items():
        image.crop(crop).save(args.output_dir / f"{label}.png")
        image.close()

    manifest = {
        "source_dir": str(args.input_dir),
        "source_size_pixels": [width, height],
        "shared_crop_pixels": crop,
        "output_size_pixels": [crop[2] - crop[0], crop[3] - crop[1]],
        "outputs": ["V19.png", "V14.png"],
    }
    (args.output_dir / "crop_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
