"""Pure image-blending worker for ghost mask application.

Merges two HDR images using a ghost mask:
  - White mask regions (1.0) → take from the noghost image
  - Black mask regions (0.0) → take from the aligned_originals image
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def apply_ghost_mask(
    aligned_path: Path,
    noghost_path: Path,
    mask_path: Path,
    output_path: Path,
) -> Path:
    """Blend two HDR merge outputs using a ghost mask.

    :param Path aligned_path: HDR merge from aligned_originals source set
    :param Path noghost_path: HDR merge from noghost source set
    :param Path mask_path: Ghost mask (white = ghost region)
    :param Path output_path: Where to write the blended result
    :return: The output path
    """
    aligned = cv2.imread(str(aligned_path), cv2.IMREAD_UNCHANGED)
    noghost = cv2.imread(str(noghost_path), cv2.IMREAD_UNCHANGED)
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)

    if aligned is None:
        raise FileNotFoundError(f"Cannot read aligned image: {aligned_path}")
    if noghost is None:
        raise FileNotFoundError(f"Cannot read noghost image: {noghost_path}")
    if mask is None:
        raise FileNotFoundError(f"Cannot read ghost mask: {mask_path}")

    h, w = aligned.shape[:2]
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    if noghost.shape[:2] != (h, w):
        noghost = cv2.resize(noghost, (w, h), interpolation=cv2.INTER_LINEAR)

    mask_float = mask.astype(np.float32) / 255.0
    if aligned.ndim == 3:
        mask_float = mask_float[:, :, np.newaxis]

    aligned_float = aligned.astype(np.float32)
    noghost_float = noghost.astype(np.float32)

    blended = noghost_float * mask_float + aligned_float * (1.0 - mask_float)
    blended = np.clip(blended, 0, np.iinfo(aligned.dtype).max if aligned.dtype != np.float32 else 1.0)
    result = blended.astype(aligned.dtype)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), result)

    return output_path
