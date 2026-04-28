from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageChops


def rms_score(reference: Image.Image, candidate: Image.Image) -> tuple[float, Image.Image]:
    ref = reference.convert("RGB")
    cand = candidate.convert("RGB").resize(ref.size)
    diff = ImageChops.difference(ref, cand)
    hist = diff.histogram()
    sq = sum(value * ((idx % 256) ** 2) for idx, value in enumerate(hist))
    rms = math.sqrt(sq / (ref.size[0] * ref.size[1] * 3))
    score = max(0.0, 100.0 * (1.0 - rms / 255.0))
    return score, diff


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--diff-output")
    args = parser.parse_args()

    score, diff = rms_score(Image.open(Path(args.reference)), Image.open(Path(args.candidate)))
    print(f"{score:.2f}")
    if args.diff_output:
        diff.save(args.diff_output)


if __name__ == "__main__":
    main()
