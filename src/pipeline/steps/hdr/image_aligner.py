import cv2
import numpy as np
import os

def align_images(path_list, output_folder="aligned"):
    # 1. Load images
    print("Loading images...")
    images = [cv2.imread(p) for p in path_list]
    
    # Check if the images were loaded correctly
    for i, img in enumerate(images):
        if img is None:
            print(f"Error: Unable to load {path_list[i]}")
            return

    # 2. Initialize the MTB aligner
    # This algorithm transforms the images into binary maps based on the median
    # to find the shift (offset) without being confused by brightness.
    print("Aligning images (MTB method)...")
    alignMTB = cv2.createAlignMTB()
    alignMTB.process(images, images)

    # 3. Create output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # 4. Save images to disk
    print(f"Saving images to '{output_folder}'...")
    for i, img in enumerate(images):
        filename = os.path.basename(path_list[i])
        save_path = os.path.join(output_folder, f"aligned_{filename}")
        cv2.imwrite(save_path, img)
        print(f"Saved: {save_path}")

    print("\nOperation completed successfully!")



def align_images_advanced(path_list, output_folder="aligned_pro"):
    # 1. Load images
    images = [cv2.imread(p) for p in path_list]
    if any(img is None for img in images):
        print("Error: Some images failed to load. Please check the paths.")
        return

    # Let's use the first image (usually the one with medium exposure) as reference
    index = 0 
    img_rif_gray = cv2.cvtColor(images[index], cv2.COLOR_BGR2GRAY)
    
    aligned_images = [None] * len(images)
    aligned_images[index] = images[index] # The reference doesn't change

    # 2. Configuration of the ECC algorithm
    # MOTION_EUCLIDEAN handles Translation + Rotation
    warp_mode = cv2.MOTION_EUCLIDEAN
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 500, 1e-8)

    print(f"Aligning images using '{path_list[index]}' as reference...")

    for i in range(len(images)):
        if i == index:
            continue
        
        print(f"  -> Aligning {path_list[i]}...")
        img_da_all_gray = cv2.cvtColor(images[i], cv2.COLOR_BGR2GRAY)
        
        # Identity transformation matrix (2x3 for Euclidean)
        warp_matrix = np.eye(2, 3, dtype=np.float32)

        try:
            # Find the correct transformation
            (cc, warp_matrix) = cv2.findTransformECC(
                img_rif_gray, img_da_all_gray, warp_matrix, warp_mode, criteria
            )
            
            # Apply the transformation to the original color image
            sz = images[index].shape
            aligned_images[i] = cv2.warpAffine(
                images[i], warp_matrix, (sz[1], sz[0]), 
                flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP
            )
        except cv2.error as e:
            print(f"  ! Warning: Alignment failed for {path_list[i]}. Using the original.")
            aligned_images[i] = images[i]

    # 3. Saving
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    for i, img in enumerate(aligned_images):
        filename = f"aligned_pro_{os.path.basename(path_list[i])}"
        cv2.imwrite(os.path.join(output_folder, filename), img)
    
    print(f"\nFatto! Immagini salvate in: {output_folder}")



def align_images_ecc(images):
    # Let's use the first image (usually the one with medium exposure) as reference
    index = 0 
    img_rif_gray = cv2.cvtColor(images[index], cv2.COLOR_BGR2GRAY)
    
    aligned_images = [None] * len(images)
    aligned_images[index] = images[index] # The reference doesn't change

    # 2. Configuration of the ECC algorithm
    # MOTION_EUCLIDEAN handles Translation + Rotation
    warp_mode = cv2.MOTION_EUCLIDEAN
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 500, 1e-8)

    for i in range(len(images)):
        if i == index:
            continue
        
        img_da_all_gray = cv2.cvtColor(images[i], cv2.COLOR_BGR2GRAY)
        
        # Identity transformation matrix (2x3 for Euclidean)
        warp_matrix = np.eye(2, 3, dtype=np.float32)

        try:
            # Find the correct transformation
            (cc, warp_matrix) = cv2.findTransformECC(
                img_rif_gray, img_da_all_gray, warp_matrix, warp_mode, criteria
            )
            
            # Apply the transformation to the original color image
            sz = images[index].shape
            aligned_images[i] = cv2.warpAffine(
                images[i], warp_matrix, (sz[1], sz[0]), 
                flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP
            )
        except cv2.error as e:
            aligned_images[i] = images[i]    

    return aligned_images