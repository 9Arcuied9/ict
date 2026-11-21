"""Check local imports and perform a small CPU operation without downloads."""
import sys
import json
import os
from importlib.metadata import version
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ.setdefault("YOLO_CONFIG_DIR", str(root / ".tools" / "ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(root / ".tools" / "matplotlib"))

import cv2
import numpy as np
import pandas
import matplotlib
import yaml
import PIL
import tqdm
import torch
import torchvision
import ultralytics

image = np.zeros((32, 32, 3), dtype=np.uint8)
assert cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).shape == (32, 32)
assert (torch.ones(2, 2) @ torch.ones(2, 2)).sum().item() == 8
boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 9., 9.]])
assert torchvision.ops.nms(boxes, torch.tensor([0.9, 0.8]), 0.5).tolist() == [0]
report = {
    "python": sys.version,
    "executable": sys.executable,
    "packages": str(Path(ultralytics.__file__).parent.parent),
    "versions": {name: version(name) for name in [
        "torch", "torchvision", "ultralytics", "opencv-python", "numpy",
        "pandas", "matplotlib", "pyyaml", "pillow", "tqdm"]},
    "cuda_available": torch.cuda.is_available(),
    "status": "Local CPU checks passed",
}
output = json.dumps(report, ensure_ascii=False, indent=2)
(root / "environment-check.json").write_text(output + "\n", encoding="utf-8")
print(output)
