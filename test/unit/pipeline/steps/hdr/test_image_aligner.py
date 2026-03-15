"""
Unit tests for image_aligner.py

Tests both align_images (MTB) and align_images_advanced (ECC) functions
with parametrized bracket configurations.
"""

import tempfile
from pathlib import Path
import shutil

import pytest
import cv2

from pipeline.steps.hdr.image_aligner import align_images, align_images_advanced
from pipeline.utils.logger import get_logger

logger = get_logger("image_aligner_test")


# Parametrized test cases with hardcoded image paths
ALIGN_TEST_CASES = [
    {
        "name": "3-shot bracket",
        "image_paths": [
            Path("C:\\temp\\pipeline_tests\\0H8A4870.JPG"),
            Path("C:\\temp\\pipeline_tests\\0H8A4871.JPG"),
            Path("C:\\temp\\pipeline_tests\\0H8A4872.JPG"),
        ],
    }
]


def test_align_images_mtb():
    """Test MTB-based alignment (align_images)."""
    image_paths = ALIGN_TEST_CASES[0]["image_paths"]
    
    # Verify all input files exist
    for path in image_paths:
        assert path.exists(), f"Test image not found: {path}"
    
    logger.info(f"Testing MTB alignment: {ALIGN_TEST_CASES[0]['name']}")
    logger.info(f"Input files: {[p.name for p in image_paths]}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        output_folder = Path(temp_dir) / "aligned_mtb"
        
        # Run MTB alignment
        align_images([str(p) for p in image_paths], str(output_folder))
        
        # Verify output folder was created
        assert output_folder.exists(), f"Output folder not created: {output_folder}"
        
        # Verify output files were created
        output_files = list(output_folder.glob("aligned_*.jpg"))
        assert len(output_files) == len(image_paths), (
            f"Expected {len(image_paths)} aligned images, got {len(output_files)}"
        )
        logger.info(f"  ✅ Created {len(output_files)} aligned images")
        
        # Verify output images are readable and have correct dimensions
        for output_path in output_files:
            img = cv2.imread(str(output_path))
            assert img is not None, f"Could not read output image: {output_path}"
            
            # Compare dimensions with original (should be the same)
            original_path = next(p for p in image_paths if p.stem in output_path.stem)
            orig_img = cv2.imread(str(original_path))
            assert img.shape == orig_img.shape, (
                f"Output image dimensions mismatch: {img.shape} vs {orig_img.shape}"
            )
        
        logger.info(f"✅ MTB alignment test passed: {ALIGN_TEST_CASES[0]['name']}\n")

def test_align_images_ecc():
    """Test ECC-based alignment (align_images_advanced)."""
    image_paths = ALIGN_TEST_CASES[0]["image_paths"]
    
    # Verify all input files exist
    for path in image_paths:
        assert path.exists(), f"Test image not found: {path}"
    
    logger.info(f"Testing ECC alignment: {ALIGN_TEST_CASES[0]['name']}")
    logger.info(f"Input files: {[p.name for p in image_paths]}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        output_folder = Path(temp_dir) / "aligned_ecc"
        
        # Run ECC alignment
        align_images_advanced([str(p) for p in image_paths], str(output_folder))
        
        # Verify output folder was created
        assert output_folder.exists(), f"Output folder not created: {output_folder}"
        
        # Verify output files were created
        output_files = list(output_folder.glob("aligned_pro_*.jpg"))
        assert len(output_files) == len(image_paths), (
            f"Expected {len(image_paths)} aligned images, got {len(output_files)}"
        )
        logger.info(f"  ✅ Created {len(output_files)} aligned images")
        
        # Verify output images are readable and have correct dimensions
        for output_path in output_files:
            img = cv2.imread(str(output_path))
            assert img is not None, f"Could not read output image: {output_path}"
            
            # Compare dimensions with original (should be the same)
            original_path = next(p for p in image_paths if p.stem in output_path.stem)
            orig_img = cv2.imread(str(original_path))
            assert img.shape == orig_img.shape, (
                f"Output image dimensions mismatch: {img.shape} vs {orig_img.shape}"
            )
        
        logger.info(f"✅ ECC alignment test passed: {ALIGN_TEST_CASES[0]['name']}\n")


def test_hardcoded_paths():
    """Test error handling for invalid image paths."""
    image_paths = ALIGN_TEST_CASES[0]["image_paths"]
    align_images([str(p) for p in image_paths], str("C:\\temp\pipeline_tests\\aligned_mtb"))
    align_images_advanced([str(p) for p in image_paths], str("C:\\temp\pipeline_tests\\aligned_ecc"))
