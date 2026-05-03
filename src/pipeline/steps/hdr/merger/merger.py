import cv2
import numpy as np
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class HdrResultMerger:
    """Merges multiple HDR image exposures using ghosting masks for artifact removal.

    This class blends bracketed HDR images using soft masks to seamlessly transition
    between exposures while removing ghosting artifacts detected in specific regions.
    """

    def __init__(self):
        """Initialize the HDR result merger."""
        pass

    def merge_hdr_results(
        self,
        hdr_with_ghost: np.ndarray,
        hdr_without_ghost: np.ndarray,
        soft_mask: np.ndarray,
    ) -> np.ndarray:
        """Merge two HDR images using a soft blending mask.

        Performs linear interpolation between two HDR exposures based on a soft mask.
        Regions where mask is 1.0 use hdr_without_ghost, regions where mask is 0.0 use hdr_with_ghost,
        and intermediate values create smooth transitions.

        Blending formula:
            Result = (hdr_without_ghost × mask) + (hdr_with_ghost × (1 - mask))

        :param np.ndarray hdr_with_ghost: First HDR image with potential ghosting (shape: H×W×3,
            dtype: float32)
        :param np.ndarray hdr_without_ghost: Second HDR image without ghosting (shape: H×W×3,
            dtype: float32)
        :param np.ndarray soft_mask: Soft blending mask (shape: H×W, dtype: float32,
            values in [0.0, 1.0])
        :return: Merged HDR image with ghosting artifacts removed
        :rtype: np.ndarray (shape: H×W×3, dtype: uint8)
        :raises ValueError: If image dimensions do not match or mask values are invalid
        """
        if hdr_with_ghost.shape[:2] != hdr_without_ghost.shape[:2]:
            raise ValueError(
                f"HDR image dimensions must match. "
                f"Got hdr_with_ghost: {hdr_with_ghost.shape}, hdr_without_ghost: {hdr_without_ghost.shape}"
            )

        if soft_mask.shape != hdr_with_ghost.shape[:2]:
            raise ValueError(
                f"Mask dimensions must match image height/width. "
                f"Got mask: {soft_mask.shape}, expected: {hdr_with_ghost.shape[:2]}"
            )

        if np.min(soft_mask) < 0.0 or np.max(soft_mask) > 1.0:
            raise ValueError(
                f"Soft mask values must be in [0.0, 1.0]. "
                f"Got range: [{np.min(soft_mask)}, {np.max(soft_mask)}]"
            )

        # Expand the mask to 3 channels (RGB) for multiplication
        mask_3d = cv2.merge([soft_mask, soft_mask, soft_mask])

        # Linear interpolation formula:
        # Result = (HDR_Without_Ghost * Mask) + (HDR_With_Ghost * (1 - Mask))
        final_img = (hdr_without_ghost * mask_3d) + (hdr_with_ghost * (1.0 - mask_3d))

        return final_img.astype(np.uint8)


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(
            "Usage: python hdr_merger.py hdr_with_ghost.jpg hdr_without_ghost.jpg soft_mask.jpg output_path"
        )
        print(
            "  hdr_with_ghost and hdr_without_ghost are the two HDR images to be merged."
        )
        print("  soft_mask is the soft mask (0.0 to 1.0) for blending.")
        print("  output_path is the path to save the merged HDR image.")
        sys.exit(1)

    hdr_with_ghost_path = sys.argv[1]
    hdr_without_ghost_path = sys.argv[2]
    soft_mask_path = sys.argv[3]
    output_path = sys.argv[4]

    hdr_with_ghost = cv2.imread(hdr_with_ghost_path).astype(np.float32)
    hdr_without_ghost = cv2.imread(hdr_without_ghost_path).astype(np.float32)
    soft_mask = (
        cv2.imread(soft_mask_path, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
    )

    merger = HdrResultMerger()
    merged_hdr = merger.merge_hdr_results(hdr_with_ghost, hdr_without_ghost, soft_mask)
    cv2.imwrite(output_path, merged_hdr)

    logger.info("HDR merge completed. Output saved to %s", output_path)
    sys.exit(0)
