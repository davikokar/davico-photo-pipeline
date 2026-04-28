import cv2
import numpy as np
from pathlib import Path
import logging
import sys
import torch
import kornia as K

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

class BracketedImagesAligner:
    """Aligns bracketed exposure images using LoFTR feature matching and homography.
    
    This class handles alignment of over/under-exposed images to a reference (middle
    exposure) image using robust feature matching with LoFTR and MAGSAC++ filtering.
    """

    def __init__(self):
        # Carichiamo LoFTR pre-addestrato (Outdoor è più robusto per scene generiche)
        self.matcher = K.feature.LoFTR(pretrained='outdoor')
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.matcher = self.matcher.to(self.device).eval()

    def _load_torch_image(self, path) -> tuple[torch.Tensor, tuple[int, int]]:
        """Load and prepare image for LoFTR processing.
        
        Converts image to grayscale tensor and normalizes to [0, 1] range.
        
        :param str path: Path to image file
        :return: Tuple of (normalized tensor, (height, width))
        :rtype: tuple[torch.Tensor, tuple[int, int]]
        """
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        h, w = img.shape
        img_tensor = K.image_to_tensor(img, keepdim=False).float() / 255.0
        return img_tensor.to(self.device), (h, w)


    def _match_and_filter(self, img1_path, img2_path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Match features between two images and filter using robust homography estimation.
        
        Workflow:
        1. Load images and resize if needed (LoFTR prefers multiples of 8/16)
        2. Detect and match keypoints using LoFTR
        3. Estimate homography with MAGSAC++ robust filtering
        4. Extract inliers for elastic warping (TPS)
        
        :param str img1_path: Path to reference image
        :param str img2_path: Path to image to align
        :return: Tuple of (H, inliers_ref, inliers_target, all_mkpts1, all_mkpts2, mask)
        :rtype: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        :raises ValueError: If fewer than 10 correspondences found
        """
        
        # Load images in BGR format for later processing
        img1_bgr = cv2.imread(str(img1_path))
        img2_bgr = cv2.imread(str(img2_path))
        h1, w1 = img1_bgr.shape[:2]
        h2, w2 = img2_bgr.shape[:2]

        # Resize for LoFTR efficiency (max 1200px per side for speed/memory)
        scale = 1200 / max(h1, w1) if max(h1, w1) > 1200 else 1.0
        w_target, h_target = int(w1 * scale), int(h1 * scale)
        
        img1_gray = cv2.cvtColor(img1_bgr, cv2.COLOR_BGR2GRAY)
        img2_gray = cv2.cvtColor(img2_bgr, cv2.COLOR_BGR2GRAY)
        
        img1_resized = cv2.resize(img1_gray, (w_target, h_target))
        img2_resized = cv2.resize(img2_gray, (w_target, h_target))

        # Prepare tensors for Kornia/LoFTR with normalization
        t1 = K.image_to_tensor(img1_resized, keepdim=False).float() / 255.0
        t2 = K.image_to_tensor(img2_resized, keepdim=False).float() / 255.0
        t1, t2 = t1.to(self.device), t2.to(self.device)

        # Match keypoints using LoFTR
        input_dict = {"image0": t1, "image1": t2}
        with torch.inference_mode():
            correspondences = self.matcher(input_dict)

        # Scale matched points back to original image resolution
        mkpts1 = correspondences['keypoints0'].cpu().numpy() / scale
        mkpts2 = correspondences['keypoints1'].cpu().numpy() / scale

        if len(mkpts1) < 10:
            raise ValueError("Troppi pochi punti di corrispondenza trovati!")

        # Robust homography estimation with MAGSAC++ (maps img2 to img1)
        H, mask = cv2.findHomography(
            mkpts2, mkpts1, 
            method=cv2.USAC_MAGSAC, 
            ransacReprojThreshold=3.0, # Tolerance of 3 pixels for static background
            maxIters=20000,
            confidence=0.999
        )

        # Extract inliers (static background points not affected by motion)
        inliers_ref = mkpts1[mask.ravel() == 1]
        inliers_target = mkpts2[mask.ravel() == 1]

        return H, inliers_ref, inliers_target, mkpts1, mkpts2, mask

    def _warp_local_tps(self, img_ref, target_img_path, pts_ref, pts_target) -> np.ndarray:
        """Apply local elastic warping using Thin Plate Spline (TPS) transformation.
        
        TPS handles local geometric distortions after global homography alignment.
        Workflow:
        1. Subsample points (TPS is slow with >2000 points)
        2. Normalize coordinates to [0, 1] range (critical for OpenCV TPS)
        3. Format data for OpenCV TPS transformer
        4. Apply TPS warp to target image
        
        :param np.ndarray img_ref: Reference image (not modified)
        :param str target_img_path: Path to image to warp
        :param np.ndarray pts_ref: Reference control points
        :param np.ndarray pts_target: Target control points
        :return: TPS-warped image
        :rtype: np.ndarray
        """        
        
        img_target = cv2.imread(str(target_img_path))
        h, w = img_ref.shape[:2]

        # Subsample to max 500 points to avoid computational explosion
        MAX_POINTS = 500
        if len(pts_ref) > MAX_POINTS:
            idx = np.random.choice(len(pts_ref), MAX_POINTS, replace=False)
            pts_ref = pts_ref[idx]
            pts_target = pts_target[idx]

        # Normalize coordinates to [0, 1] range (OpenCV requirement for TPS)
        pts_ref_norm = pts_ref.copy()
        pts_target_norm = pts_target.copy()
        
        pts_ref_norm[:, 0] /= w
        pts_ref_norm[:, 1] /= h
        pts_target_norm[:, 0] /= w
        pts_target_norm[:, 1] /= h

        # Format points as (1, N, 2) float32 for OpenCV
        source_pts = pts_target_norm.reshape(1, -1, 2).astype(np.float32)
        destination_pts = pts_ref_norm.reshape(1, -1, 2).astype(np.float32)

        # Create 1-to-1 match pairs
        matches = [cv2.DMatch(i, i, 0) for i in range(len(pts_ref))]

        # Estimate and apply TPS transformation
        tps = cv2.createThinPlateSplineShapeTransformer()
        tps.estimateTransformation(source_pts, destination_pts, matches)
        warped_img = tps.warpImage(img_target)
        
        return warped_img



    def align(self, ref_image_path, normalized_images_paths, original_images_paths, output_folder) -> tuple[list, list]:
        """Align bracketed images to reference using global homography.
        
        Aligns both normalized and original (raw exposure) images to the reference
        image. Saves intermediate results to disk.
        
        :param str ref_image_path: Path to reference (middle exposure) image
        :param list normalized_images_paths: Paths to exposure-normalized images
        :param list original_images_paths: Paths to original exposure images
        :param str output_folder: Output directory for aligned images
        :return: Tuple of (aligned_normalized_list, aligned_original_list)
        :rtype: tuple[list, list]
        """        
        
        output_path = Path(output_folder)
        output_path.mkdir(exist_ok=True)
        
        # Load reference image to get dimensions
        ref_image = cv2.imread(str(ref_image_path))
        h_ref, w_ref = ref_image.shape[:2] # Qui otteniamo H e W reali

        # The first image is already aligned to itself
        images_original_aligned = [ref_image]
        images_normalized_aligned = [ref_image]
        i = 0
        
        for normalized_image_path, original_image_path in zip(normalized_images_paths, original_images_paths):
            print(f"Analizzo {Path(normalized_image_path).name} per allineare {Path(original_image_path).name}")
            i += 1

            # Compute alignment using normalized images (more stable features)
            H, inliers_ref, inliers_target, all_p1, all_p2, mask = self._match_and_filter(ref_image_path, normalized_image_path)
            
            # Load original (over/underexposed) and normalized images for diagnostics and deghosting
            # and the normalized images for diagnostics and deghosting
            original_image = cv2.imread(str(original_image_path))
            normalized_image = cv2.imread(str(normalized_image_path))

            # Apply global homography transformation
            original_image_warped_global = cv2.warpPerspective(original_image, H, (w_ref, h_ref))
            normalized_image_warped_global = cv2.warpPerspective(normalized_image, H, (w_ref, h_ref))

            # Save aligned images for comparison
            original_stem = Path(original_image_path).stem
            original_ext = Path(original_image_path).suffix
            original_aligned_filename = f"{original_stem}_original_aligned{original_ext}"
            cv2.imwrite(str(output_path / original_aligned_filename), original_image_warped_global)
            
            normalized_stem = Path(normalized_image_path).stem
            normalized_ext = Path(normalized_image_path).suffix
            normalized_aligned_filename = f"{normalized_stem}_normalized_aligned{normalized_ext}"
            cv2.imwrite(str(output_path / normalized_aligned_filename), normalized_image_warped_global)

            # Append the aligned images to the result lists
            images_original_aligned.append(original_image_warped_global)
            images_normalized_aligned.append(normalized_image_warped_global)
        
        return (images_normalized_aligned, images_original_aligned)


    def create_checkerboard_comparison(
        img_a: np.ndarray,
        img_b: np.ndarray,
        block_size: int = 64,
    ) -> np.ndarray:
        """Create checkerboard visualization comparing two aligned images.
        
        Alternates blocks from img_a and img_b to visually verify alignment quality.
        Useful for quick assessment of homography accuracy.
        
        :param np.ndarray img_a: First image (baseline)
        :param np.ndarray img_b: Second image (aligned)
        :param int block_size: Size of checkerboard blocks in pixels. Default: 64
        :return: Checkerboard composite image
        :rtype: np.ndarray
        """
        h, w = img_a.shape[:2]
        result = img_a.copy()

        for y in range(0, h, block_size):
            for x in range(0, w, block_size):
                if ((x // block_size) + (y // block_size)) % 2 == 0:
                    y_end = min(y + block_size, h)
                    x_end = min(x + block_size, w)
                    result[y:y_end, x:x_end] = img_b[y:y_end, x:x_end]

        return result


    def create_difference_image(
        img_a: np.ndarray,
        img_b: np.ndarray,
        amplification: float = 3.0,
    ) -> np.ndarray:
        """Generate amplified difference image to highlight misalignment regions.
        
        Areas with significant pixel differences indicate ghosting or residual
        misalignment after correction. High amplification helps visualize subtle errors.
        
        :param np.ndarray img_a: First image
        :param np.ndarray img_b: Second image
        :param float amplification: Multiplication factor for difference values.
            Default: 3.0
        :return: Amplified difference image (uint8, [0, 255])
        :rtype: np.ndarray
        """
        diff = cv2.absdiff(img_a, img_b)
        diff_amplified = np.clip(diff.astype(np.float32) * amplification, 0, 255).astype(np.uint8)
        return diff_amplified


# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) != 7:
        print("Usage: python aligner.py ref_image.jpg normalized_over.jpg normalized_under.jpg original_over.jpg original_under.jpg output_folder")
        print("  The ref image is the middle exposure one and is used as reference for the alignment.")
        print("  The normalized images are the over and under exposed images after exposure normalization.")
        print("  The original images are the over and under exposed images before exposure normalization.")
        print("  The output folder is where the aligned images and diagnostics will be saved.")
        sys.exit(1)

    ref_image_path = sys.argv[1]
    normalized_images_paths = [sys.argv[2], sys.argv[3]]
    original_images_paths = [sys.argv[4], sys.argv[5]]
    output_folder = sys.argv[6]
    

    aligner = BracketedImagesAligner()
    aligned_normalized, aligned_original = aligner.align(ref_image_path, normalized_images_paths, original_images_paths, output_folder)


    # Diagnostics image generation
    out = Path(output_folder)
    for i in [1, 2]:
        checker_normalized = aligner.create_checkerboard_comparison(aligned_normalized[0], aligned_normalized[i])
        cv2.imwrite(str(out / f"checker_normalized_ref_vs_{i}.jpg"), checker_normalized)
        checker_original = aligner.create_checkerboard_comparison(aligned_original[0], aligned_original[i])
        cv2.imwrite(str(out / f"checker_original_ref_vs_{i}.jpg"), checker_original)

        diff_normalized = aligner.create_difference_image(aligned_normalized[0], aligned_normalized[i])
        cv2.imwrite(str(out / f"diff_normalized_ref_vs_{i}.jpg"), diff_normalized)
        diff_original = aligner.create_difference_image(aligned_original[0], aligned_original[i])
        cv2.imwrite(str(out / f"diff_original_ref_vs_{i}.jpg"), diff_original)

    logger.info("Done! Check the diagnostic images.")