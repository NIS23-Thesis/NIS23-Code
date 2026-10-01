'''

This code allows the user to generate the MNIST dataset from the original 
npz file. The generated images are saved in a folder structure that is 
compatible with PyTorch's ImageFolder dataset class.

File structure: 
<directory of your choice>/
└── nmist/
    ├── train/
    │   ├── 0/
    │   ├── 1/
    │   └── ... 9/
    └── test/
        ├── 0/
        ├── 1/
        └── ... 9/
'''

import numpy as np
from PIL import Image
from pathlib import Path

# Locate the NPZ file in Downloads, but you can change the directory
downloads = Path.home() / "Downloads"
data = np.load(downloads / "mnist.npz")

# Create the nmist folder in Downloads
output = downloads / "nmist"

for split in ["train", "test"]:
    images = data[f"x_{split}"]
    labels = data[f"y_{split}"]

    for i, (image, label) in enumerate(zip(images, labels)):
        folder = output / split / str(label)
        folder.mkdir(parents=True, exist_ok=True)

        Image.fromarray(image).save(folder / f"{i}.png")

print(f"All images saved to: {output}")