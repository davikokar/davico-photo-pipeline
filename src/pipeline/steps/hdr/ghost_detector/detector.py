import cv2
import numpy as np
from pathlib import Path
import logging
import sys


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class GhostDetector:
    """Detects ghosting artifacts in aligned images through multi-stage morphological
    analysis and connected component filtering.
    
    This class uses a combination of thresholding, morphological operations, and
    area-based filtering to identify regions with significant ghosting while
    ignoring sensor noise and micro-misalignments.
    """

    def __init__(
        self,
        threshold: int = 20,
        min_area: int = 50,
        dilation_size: int = 31,
        blur_size: int = 151,
        kernel_size: tuple = (5, 5),
    ):
        """Initialize the GhostDetector with processing parameters.
        
        :param int threshold: Threshold value for binary segmentation (0-255).
            Higher values are more selective. Default: 20
        :param int min_area: Minimum area of connected components to keep (pixels).
            Smaller components are filtered out. Default: 50
        :param int dilation_size: Size of the dilation kernel for solidifying ghost
            regions. Must be positive odd integer. Default: 31
        :param int blur_size: Size of Gaussian blur kernel for soft mask creation.
            Must be positive odd integer. Default: 151
        :param tuple kernel: Size of the morphological kernel for cleaning. Default: (5, 5)
        """

        self.threshold = threshold
        self.min_area = min_area
        self.dilation_size = dilation_size
        self.blur_size = blur_size
        self.kernel_size = kernel_size

    def detect_ghost_mask(
        self, ref_image_path: str, aligned_image_path: str
    ) -> np.ndarray:
        """Detect ghosting artifacts by comparing reference and aligned images.
        
        Performs the following steps:
        1. Load and compute absolute difference in grayscale
        2. Binary thresholding to isolate moving regions
        3. Morphological cleaning (open and close operations)
        4. Connected component analysis with area filtering
        5. Dilation to solidify ghost regions
        6. Gaussian blur for smooth mask transitions
        
        :param str ref_image_path: Path to reference (middle exposure) image
        :param str aligned_image_path: Path to aligned image
        :return: Ghost mask with values in [0.0, 1.0] range
        :rtype: np.ndarray
        """
        ref_image = cv2.imread(str(ref_image_path))
        aligned_image = cv2.imread(str(aligned_image_path))

        if ref_image is None or aligned_image is None:
            raise FileNotFoundError(
                "Could not load images. Check file paths and formats."
            )

        # 1. Compute absolute difference in grayscale
        diff = cv2.absdiff(ref_image, aligned_image)
        if len(diff.shape) == 3:
            diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

        # 2. Binary threshold: white = motion, black = static
        # Ignores sensor noise and micro-alignment errors
        _, mask = cv2.threshold(diff, self.threshold, 255, cv2.THRESH_BINARY)

        # 3. Morphological cleaning
        # MORPH_OPEN: erode thin lines, then dilate to restore blocks
        # MORPH_CLOSE: fill small holes within ghost regions
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, self.kernel_size)
        mask_cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask_cleaned = cv2.morphologyEx(mask_cleaned, cv2.MORPH_CLOSE, kernel)

        # 4. Connected components analysis with area filtering
        # Keep only components larger than min_area threshold
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            mask_cleaned
        )

        seeds = np.zeros_like(mask_cleaned)
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] > self.min_area:
                seeds[labels == i] = 255

        # 5. Solidification through dilation
        # Expand seeds to cover entire ghost objects (limbs, shadows, etc.)
        if self.dilation_size > 0:
            kernel_dilate = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (self.dilation_size, self.dilation_size)
            )
            seeds = cv2.dilate(seeds, kernel_dilate, iterations=1)
            seeds = cv2.morphologyEx(seeds, cv2.MORPH_CLOSE, kernel_dilate)

        # 6. Gaussian blur for soft mask transitions (required for HDR merging)
        blur_size = self.blur_size
        if blur_size > 0:
            # Ensure blur_size is odd for symmetric Gaussian
            if blur_size % 2 == 0:
                blur_size += 1
            final_ghost_mask = cv2.GaussianBlur(
                seeds, (blur_size, blur_size), 0
            )
        else:
            final_ghost_mask = seeds

        # Convert to float [0.0, 1.0] range for downstream HDR merging
        return final_ghost_mask.astype(np.float32) / 255.0


    def visualize_ghosts(self, ref_image_path: str, ghost_mask: np.ndarray) -> np.ndarray:
        """Create visualization of detected ghost regions overlaid on reference image.
        
        Overlays detected ghosts in red with 30% opacity over the reference image
        for visual verification.
        
        :param str ref_image_path: Path to reference image
        :param np.ndarray ghost_mask: Ghost mask with values in [0.0, 1.0]
        :return: Composite image with ghost overlay
        :rtype: np.ndarray
        """
        img_ref = cv2.imread(str(ref_image_path))

        if img_ref is None:
            raise FileNotFoundError(f"Could not load reference image: {ref_image_path}")

        overlay = img_ref.copy()
        # Color detected ghost regions (mask > 0) in red
        overlay[ghost_mask > 0] = [0, 0, 255]

        # Blend original with overlay for semi-transparent visualization
        combined = cv2.addWeighted(img_ref, 0.7, overlay, 0.3, 0)
        return combined


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) != 6:
        print("Usage: python ghost_detector.py ref_image.jpg aligned_image.jpg threshold min_area output_folder")
        print("  The ref image is the middle exposure one.")
        print("  The aligned image is the result of the alignment process.")
        print("  The threshold is the value used to detect significant differences.")
        print("  The min_area is the minimum area of detected ghosting regions.")
        print("  The output folder is where the diagnostic images will be saved.")
        sys.exit(1)

    ref_image_path = sys.argv[1]
    aligned_image_path = sys.argv[2]
    threshold = float(sys.argv[3])
    min_area = int(sys.argv[4])
    output_folder = sys.argv[5]
    
    logger.info("Ghost detector started.")

    detector = GhostDetector(threshold=threshold, min_area=min_area)

    ghost_mask = detector.detect_ghost_mask(ref_image_path, aligned_image_path)

    # Diagnostics image generation
    ref_image_with_mask = detector.visualize_ghosts(ref_image_path, ghost_mask)

    out = Path(output_folder)
    out.mkdir(parents=True, exist_ok=True)
    mask_to_save = (ghost_mask * 255).astype(np.uint8)
    cv2.imwrite(str(out / "ghost_mask.jpg"), mask_to_save)
    cv2.imwrite(str(out / "ref_image_with_mask.jpg"), ref_image_with_mask)

    logger.info("Done! Check the diagnostic images.")