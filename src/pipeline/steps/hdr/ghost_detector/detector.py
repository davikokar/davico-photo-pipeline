import cv2
import numpy as np
from pathlib import Path
import logging
import sys
from skimage.exposure import match_histograms


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class GhostDetector:
    """Detects ghosting artifacts in aligned bracket images using multi-scale SSIM
    and chrominance difference with adaptive thresholding.
    """

    def __init__(
        self,
        threshold: int = 50,
        min_area: int = 50,
        dilation_size: int = 31,
        blur_size: int = 151,
        kernel_size: tuple = (5, 5),
        ssim_scales: list | None = None,
        chroma_weight: float = 0.8,
        chroma_blur_size: int = 11,
        chroma_normalization: float = 80.0,
        adaptive_threshold: bool = True,
        threshold_min: int = 25,
        threshold_max: int = 120,
        clip_low: int = 10,
        clip_high: int = 245,
    ):
        self.threshold = threshold
        self.min_area = min_area
        self.dilation_size = dilation_size
        self.blur_size = blur_size
        self.kernel_size = kernel_size
        self.ssim_scales = ssim_scales or [7, 15, 31]
        self.chroma_weight = chroma_weight
        self.chroma_blur_size = chroma_blur_size
        self.chroma_normalization = chroma_normalization
        self.adaptive_threshold = adaptive_threshold
        self.threshold_min = threshold_min
        self.threshold_max = threshold_max
        self.clip_low = clip_low
        self.clip_high = clip_high

    def detect_ghost_mask(
        self,
        ref_image_path: str,
        normalized_image_path: str,
        original_image_path: str,
    ) -> np.ndarray:
        """Detect ghosting artifacts by comparing reference and normalized images.

        Uses the original (non-normalized) image to build a validity mask that
        excludes clipped highlights and crushed shadows, then performs multi-scale
        SSIM on luminance and Euclidean distance on chrominance (LAB color space)
        between reference and normalized images.

        :param str ref_image_path: Path to reference (middle exposure) image
        :param str normalized_image_path: Path to aligned, exposure-normalized image
        :param str original_image_path: Path to aligned, non-normalized image (used for clip mask)
        :return: Ghost mask with values in [0.0, 1.0] range
        :rtype: np.ndarray
        """
        ref_image = cv2.imread(str(ref_image_path))
        normalized_image = cv2.imread(str(normalized_image_path))
        original_image = cv2.imread(str(original_image_path))

        if ref_image is None or normalized_image is None or original_image is None:
            raise FileNotFoundError(
                "Could not load images. Check file paths and formats."
            )

        # Build validity mask from the original (non-normalized) bracket image
        # where clipped highlights/shadows are still identifiable
        ref_gray = cv2.cvtColor(ref_image, cv2.COLOR_BGR2GRAY).astype(np.float32)
        original_gray = cv2.cvtColor(original_image, cv2.COLOR_BGR2GRAY).astype(np.float32)
        valid = (
            (ref_gray > self.clip_low) & (ref_gray < self.clip_high)
            & (original_gray > self.clip_low) & (original_gray < self.clip_high)
        )

        aligned_norm = match_histograms(
            normalized_image, ref_image, channel_axis=-1
        ).astype(ref_image.dtype)

        ref_lab = cv2.cvtColor(ref_image, cv2.COLOR_BGR2LAB).astype(np.float32)
        aligned_lab = cv2.cvtColor(aligned_norm, cv2.COLOR_BGR2LAB).astype(np.float32)

        ref_L = ref_lab[:, :, 0]
        aligned_L = aligned_lab[:, :, 0]

        luma_dissimilarity = self._compute_ssim_dissimilarity(ref_L, aligned_L)
        chroma_dissimilarity = self._compute_chroma_dissimilarity(ref_lab, aligned_lab)

        combined = np.maximum(luma_dissimilarity, chroma_dissimilarity * self.chroma_weight)
        combined[~valid] = 0

        diff = self._apply_threshold(combined)




        # Morphological cleaning
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, self.kernel_size)
        mask_cleaned = cv2.morphologyEx(diff, cv2.MORPH_OPEN, kernel)
        mask_cleaned = cv2.morphologyEx(mask_cleaned, cv2.MORPH_CLOSE, kernel)

        # Connected components with area filtering
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_cleaned)
        seeds = np.zeros_like(mask_cleaned)
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] > self.min_area:
                seeds[labels == i] = 255

        # Dilation to solidify ghost regions
        if self.dilation_size > 0:
            kernel_dilate = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (self.dilation_size, self.dilation_size)
            )
            seeds = cv2.dilate(seeds, kernel_dilate, iterations=1)
            seeds = cv2.morphologyEx(seeds, cv2.MORPH_CLOSE, kernel_dilate)

        # Gaussian blur for soft mask transitions
        blur_size = self.blur_size
        if blur_size > 0:
            if blur_size % 2 == 0:
                blur_size += 1
            final_ghost_mask = cv2.GaussianBlur(seeds, (blur_size, blur_size), 0)
        else:
            final_ghost_mask = seeds

        return final_ghost_mask.astype(np.float32) / 255.0

    def _compute_ssim_dissimilarity(
        self, ref_gray: np.ndarray, aligned_gray: np.ndarray
    ) -> np.ndarray:
        """Multi-scale structural dissimilarity map (luminance-invariant).

        Uses only the contrast-structure component of SSIM, dropping the luminance
        term entirely. This makes the comparison purely about texture/edges,
        ignoring local brightness differences between brackets.
        """
        C2 = (0.03 * 255) ** 2

        dissimilarity_maps = []
        for win_size in self.ssim_scales:
            if win_size % 2 == 0:
                win_size += 1
            sigma = win_size / 6.0
            ksize = (win_size, win_size)

            mu_a = cv2.GaussianBlur(ref_gray, ksize, sigma)
            mu_b = cv2.GaussianBlur(aligned_gray, ksize, sigma)

            sigma_a_sq = cv2.GaussianBlur(ref_gray ** 2, ksize, sigma) - mu_a ** 2
            sigma_b_sq = cv2.GaussianBlur(aligned_gray ** 2, ksize, sigma) - mu_b ** 2
            sigma_ab = cv2.GaussianBlur(ref_gray * aligned_gray, ksize, sigma) - mu_a * mu_b

            # Contrast-structure only: invariant to local mean (brightness)
            numerator = 2 * sigma_ab + C2
            denominator = sigma_a_sq + sigma_b_sq + C2

            cs_map = numerator / denominator
            dissimilarity = np.clip((1.0 - cs_map) / 2.0, 0, 1)
            dissimilarity_maps.append(dissimilarity)

        return np.maximum.reduce(dissimilarity_maps).astype(np.float32)

    def _compute_chroma_dissimilarity(
        self, ref_lab: np.ndarray, aligned_lab: np.ndarray
    ) -> np.ndarray:
        """Euclidean distance in a*b* space, normalized to [0, 1]."""
        k = self.chroma_blur_size
        if k % 2 == 0:
            k += 1
        ksize = (k, k)

        ref_a = cv2.GaussianBlur(ref_lab[:, :, 1], ksize, 0)
        ref_b = cv2.GaussianBlur(ref_lab[:, :, 2], ksize, 0)
        ali_a = cv2.GaussianBlur(aligned_lab[:, :, 1], ksize, 0)
        ali_b = cv2.GaussianBlur(aligned_lab[:, :, 2], ksize, 0)

        dist = np.sqrt((ref_a - ali_a) ** 2 + (ref_b - ali_b) ** 2)
        return np.clip(dist / self.chroma_normalization, 0, 1).astype(np.float32)

    def _apply_threshold(self, combined: np.ndarray) -> np.ndarray:
        """Convert float32 dissimilarity map [0,1] to binary uint8 mask."""
        diff_uint8 = (combined * 255).astype(np.uint8)

        if self.adaptive_threshold:
            nonzero_pixels = diff_uint8[diff_uint8 > 5]
            if len(nonzero_pixels) > 100:
                otsu_thresh, _ = cv2.threshold(
                    nonzero_pixels, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
                )
                effective_threshold = int(np.clip(otsu_thresh, self.threshold_min, self.threshold_max))
            else:
                effective_threshold = self.threshold
        else:
            effective_threshold = self.threshold

        _, mask = cv2.threshold(diff_uint8, effective_threshold, 255, cv2.THRESH_BINARY)
        return mask

    def visualize_ghosts(
        self, ref_image_path: str, ghost_mask: np.ndarray
    ) -> np.ndarray:
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
    if len(sys.argv) != 7:
        print(
            "Usage: python ghost_detector.py ref_image.jpg normalized_image.jpg original_image.jpg threshold min_area output_folder"
        )
        print("  The ref image is the middle exposure one.")
        print("  The normalized image is the aligned, exposure-normalized bracket.")
        print("  The original image is the aligned, non-normalized bracket (used for clip mask).")
        print("  The threshold is the value used to detect significant differences.")
        print("  The min_area is the minimum area of detected ghosting regions.")
        print("  The output folder is where the diagnostic images will be saved.")
        sys.exit(1)

    ref_image_path = sys.argv[1]
    normalized_image_path = sys.argv[2]
    original_image_path = sys.argv[3]
    threshold = float(sys.argv[4])
    min_area = int(sys.argv[5])
    output_folder = sys.argv[6]

    logger.info("Ghost detector started.")

    detector = GhostDetector(threshold=threshold, min_area=min_area)

    ghost_mask = detector.detect_ghost_mask(ref_image_path, normalized_image_path, original_image_path)

    # Diagnostics image generation
    ref_image_with_mask = detector.visualize_ghosts(ref_image_path, ghost_mask)

    out = Path(output_folder)
    out.mkdir(parents=True, exist_ok=True)
    mask_to_save = (ghost_mask * 255).astype(np.uint8)
    cv2.imwrite(str(out / "ghost_mask.jpg"), mask_to_save)
    cv2.imwrite(str(out / "ref_image_with_mask.jpg"), ref_image_with_mask)

    logger.info("Done! Check the diagnostic images.")
