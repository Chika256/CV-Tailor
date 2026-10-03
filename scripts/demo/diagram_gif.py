"""Turn recorded diagram steps into an animated GIF.

Usage: python scripts/demo/diagram_gif.py scripts/demo/out/diagram/tailor-dark docs/architecture-dark.gif
"""

import sys
from pathlib import Path

from PIL import Image

WIDTH, STEP_MS, LAST_MS = 1000, 2200, 3600


def main(frames_dir: Path, out: Path) -> None:
    images = []
    for file in sorted(frames_dir.glob("*.png")):
        image = Image.open(file).convert("RGB")
        images.append(image.resize((WIDTH, round(image.height * WIDTH / image.width)), Image.Resampling.LANCZOS))
    # A palette per frame from octree quantisation: a shared median-cut palette, dominated by the dark
    # background, washed out the small coloured accents (active wires, legend swatches) that carry meaning.
    frames = [
        image.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE) for image in images
    ]
    durations = [STEP_MS] * (len(frames) - 1) + [LAST_MS]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print(out.name, out.stat().st_size, images[0].size, len(frames), "frames")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
