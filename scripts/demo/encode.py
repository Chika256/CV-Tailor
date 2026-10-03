"""Encode the frames from render_video.mjs as an animated GIF or WebP, chosen by the target's extension.

Usage: python scripts/demo/encode.py docs/demo.webp [--quality 78] [--width 960] [--step 2]
       python scripts/demo/encode.py scripts/demo/out/video/demo.gif   (after render_video.mjs --no-grain)

The video is designed for this: between movements every pixel holds still, so identical frames merge into
one longer frame, and a moving frame stores only the rectangle that changed. For the GIF, one palette is
shared by every frame, so the still background maps to the same colours each time and costs nothing to
repeat.
"""

import argparse
import json
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent / "out" / "video"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--step", type=int, default=2, help="keep every Nth frame (2: 25 fps becomes 12.5 fps)")
    parser.add_argument("--quality", type=int, default=78, help="WebP only: lossy quality, 0-100")
    args = parser.parse_args()
    composition = json.loads((OUT / "composition.json").read_text(encoding="utf-8"))
    files = sorted((OUT / "frames").glob("*.png"))
    if len(files) != composition["durationInFrames"]:
        raise SystemExit(f"expected {composition['durationInFrames']} frames, found {len(files)}: render first")
    size = (args.width, round(composition["height"] * args.width / composition["width"]))
    images = [Image.open(file).convert("RGB").resize(size, Image.Resampling.LANCZOS) for file in files[:: args.step]]
    duration = 1000 * args.step / composition["fps"]

    if args.target.suffix == ".gif":
        if composition.get("grain"):
            print("note: rendered with grain; `render_video.mjs --no-grain` makes a much smaller GIF")
        # The palette comes from a sample of frames across the whole video, stacked into one image.
        sample = images[:: max(1, len(images) // 24)]
        source = Image.new("RGB", (size[0], size[1] * len(sample)))
        for index, image in enumerate(sample):
            source.paste(image, (0, size[1] * index))
        palette = source.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        # No dithering: dither noise differs wherever anything moves, which would defeat the frame deltas.
        frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
        frames[0].save(args.target, save_all=True, append_images=frames[1:], duration=duration, loop=0,
                       optimize=True, disposal=1)
    elif args.target.suffix == ".webp":
        # Full colour, so no banding in the gradients. Lossy sub-frames can leave a faint trace of a card's
        # soft shadow after it leaves, because nearly unchanged pixels are not re-encoded; a keyframe at
        # least every 40 frames (3.2 s) clears any trace. minimize_size would turn keyframes off.
        images[0].save(args.target, save_all=True, append_images=images[1:], duration=duration, loop=0,
                       quality=args.quality, method=4, kmin=20, kmax=40)
    else:
        raise SystemExit("the target must end in .gif or .webp")
    stored = Image.open(args.target).n_frames
    print(f"{args.target}: {args.target.stat().st_size / 1024:.0f} KB, {size[0]}x{size[1]}, "
          f"{len(images)} frames stored as {stored}, {len(images) * duration / 1000:.1f} s")


if __name__ == "__main__":
    main()
