'''
This script corrupts a set of images by applying various 
types of noise and artifacts for testing with UDP Socket.
'''

import csv, shutil
import numpy as np

from pathlib import Path
from collections import Counter
from PIL import Image, ImageFilter

INPUT_DIR = Path(__file__).parent / "test-files/clean"
OUTPUT_DIR = Path(__file__).parent / "test-files/corrupted"

MIN_CORRUPTION = 0.4
MAX_CORRUPTION = 0.96

rng = np.random.default_rng(50)

SUPPORTED_FORMATS = [".png", ".jpg", ".jpeg"]

# different methods of corruption
METHODS = [
    "gaussian_noise",
    "salt_pepper",
    "pixel_dropout",
    "block_corruption",
    "gaussian_blur",
    "bit_flip",
]

def corrupt_image(image, method):
    pixels = np.array(image.convert("L"), copy=True)

    if method == "gaussian_noise":
        sigma = float(rng.uniform(30, 80))
        noise = rng.normal(0, sigma, pixels.shape)

        pixels = np.clip(
            pixels.astype(float) + noise, 0, 255
        ).astype(np.uint8)

        return Image.fromarray(pixels), f"sigma={sigma:.2f}"

    elif method == "salt_pepper":
        probability = float(rng.uniform(0.10, 0.30))
        mask = rng.random(pixels.shape)

        pixels[mask < probability / 2] = 0
        pixels[
            (mask >= probability / 2)
            & (mask < probability)
        ] = 255

        return Image.fromarray(pixels), f"p={probability:.3f}"

    elif method == "pixel_dropout":
        probability = float(rng.uniform(0.15, 0.40))
        mask = rng.random(pixels.shape) < probability

        pixels[mask] = 0

        return Image.fromarray(pixels), f"p={probability:.3f}"

    elif method == "block_corruption":
        height, width = pixels.shape

        block_w = int(rng.integers(5, min(17, width + 1)))
        block_h = int(rng.integers(5, min(17, height + 1)))

        x = int(rng.integers(0, width - block_w + 1))
        y = int(rng.integers(0, height - block_h + 1))

        pixels[y:y + block_h, x:x + block_w] = 0

        return (
            Image.fromarray(pixels),
            f"x={x},y={y},w={block_w},h={block_h}",
        )

    elif method == "gaussian_blur":
        radius = float(rng.uniform(1.0, 2.5))

        result = image.convert("L").filter(
            ImageFilter.GaussianBlur(radius)
        )

        return result, f"radius={radius:.2f}"

    elif method == "bit_flip":
        probability = float(rng.uniform(0.05, 0.20))
        mask = rng.random(pixels.shape) < probability

        for idc in np.argwhere(mask):
            y, x = idc
            value = int(pixels [y, x])
            bit_to_flip = rng.integers(0, 8)
            value ^= (1 << bit_to_flip)
            pixels[y, x] = np.clip(value, 0, 255)

        return Image.fromarray(pixels), f"p={probability:.3f}"
    
    raise ValueError(f"Unknown corruption method: {method}")

def main():
    if not INPUT_DIR.is_dir():
        raise FileNotFoundError(
            f"Input directory not found: {INPUT_DIR}"
        )

    # Find images, including those in subdirectories.
    images = sorted(
        path for path in INPUT_DIR.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_FORMATS
    )

    total = len(images)

    if total == 0:
        raise ValueError("No images found in input directory.")

    # Randomly select a percentage between 60% and 70%.
    rate = float(
        rng.uniform(MIN_CORRUPTION, MAX_CORRUPTION)
    )

    count = round(total * rate)

    # Select images without replacement.
    selected = set(
        rng.choice(total, size=count, replace=False).tolist()
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    counts = Counter()
    log_path = OUTPUT_DIR / "corruption_log.csv"

    with log_path.open("w", newline="") as log_file:
        writer = csv.writer(log_file)

        writer.writerow([
            "filename",
            "relative_path",
            "corrupted",
            "method",
            "parameters",
        ])

        for i, path in enumerate(images):
            # Preserve the input's directory structure.
            relative_path = path.relative_to(INPUT_DIR)
            destination = OUTPUT_DIR / relative_path

            destination.parent.mkdir(
                parents=True, exist_ok=True
            )

            if i in selected:
                method = str(rng.choice(METHODS))

                with Image.open(path) as img:
                    result, parameters = corrupt_image(
                        img, method
                    )

                result.save(destination)
                counts[method] += 1
                corrupted = True

            else:
                shutil.copy2(path, destination)

                method = "none"
                parameters = ""
                corrupted = False

            writer.writerow([
                path.name,
                str(relative_path),
                corrupted,
                method,
                parameters,
            ])

    print("\n===== CORRUPTION SUMMARY =====")
    print(f"Total images: {total}")
    print(f"Selected rate: {rate:.2%}")
    print(f"Corrupted: {count}")
    print(f"Unchanged: {total - count}")

    for method in METHODS:
        print(f"{method}: {counts[method]}")

    print(f"\nOutput: {OUTPUT_DIR}")
    print(f"Log: {log_path}")


if __name__ == "__main__":
    main()
