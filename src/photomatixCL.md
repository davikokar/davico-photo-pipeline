# Photomatix Command Line utility

Usage: PhotomatixCL [arguments] source-images

Command line arguments:

-0      Combination of the source images with Average
-1      Combination of the source images with Fusion/Auto

-2      Combination of the source images with Fusion/Natural
-2a     Accentuation for method "Natural" e.g. -2a 0.5  (valid values vary between -10 and 10)
-2b     Blending Point for method "Natural" e.g. -2b 0.5  (valid values vary between -10 and 10)
-2c     Color Saturation for method "Natural" e.g. -2c 0.5  (valid values vary between -10 and 10)
-2h     Sharpness for method "Natural" e.g. -2h 0.5  (valid values vary between 0 and 10)
-2k     Black Point for method "Natural" e.g. -2k 0.5  (valid values vary between 0 and 10)
-2m     Midtone for method "Natural" e.g. -2m 0.5  (valid values vary between -10 and 10)
-2s     Shadows for method "Natural" e.g. -2s 0.5  (valid values vary between -10 and 10)
-2w     White Point for method "Natural" e.g. -2w 0.5  (valid values vary between 0 and 10)

-4      Combination of the source images with Fusion/Intensive
-4c     Color Saturation for method "Intensive" e.g. -4c 0.5  (valid values vary between -10 and 10)
-4r     Radius for method "Intensive" e.g. -4r 30  (valid values vary between 10 and 70)
-4s     Strength for method "Intensive" e.g. -4s 0.5  (valid values vary between -10 and 10)

-5      Combination of the source images with Fusion/Realistic
-5a     Strength for method "Realistic" e.g. -2a 0.5  (valid values vary between -10 and 10)
-5c     Color Saturation for method "Realistic" e.g. -2c 0.5  (valid values vary between -10 and 10)
-5h     Sharpness for method "Realistic" e.g. -2h 0.5  (valid values vary between 0 and 10)

-3      Merge of the source images into an HDR image (by default saved in Radiance ".hdr" format)
-h      HDR saving options: -h "exr" will save the HDR image in the OpenEXR format, -h "tif32" will save in 32-bit floating point TIFF, -h "remove" will remove the HDR image after having tone mapped it.
-hn Save HDR as normalized image - applies only if -h tif32 option is specified
-e      EV spacing for merging to HDR when exposure information not found on source files, e.g. -e 2.0 for images with a two EVs difference between them.
-g      Attempt to reduce ghosting artifacts: -gn for normal detection, -gh for high detection
-cu     Tonal curve for HDR generation. Options are -cu 0 for tonal curve of ICC profiles of source images (default), -cu 1 for attempting to recover tone curve applied, -cu 1
-wb     Color temperature for RAW conversion when source images are RAW files, e.g. -wb 7500
-mh     Merge to HDR image strip by strip instead of loading the whole source files in memory. Requires option -3 and TIFF files as source. Alignment and ghosting reducing options will be disabled
-md     Use scratch disk option for Details Enhancer and Fusion/Natural methods (reduces needed memory by a factor of 3). This option is automatically enabled if -mh option is used.
-mp Specify maximum number of CPUs to use when processing images - e.g. -mp 4 to use up to 4 CPUs
-sd     Scratch directory to use for the temporary files when required (without this option the standard temp directory will be used), e.g. -sd E:\temp

-a1     Alignment of the source images by correcting shifts prior to combining them
-a2 Alignment of the source images by matching features
-a3 Alignment of the source images by matching features, without perspective correction
-a1n    (or -a2n or -a3n) Align images but do not crop the aligned results
-a1s    (or -a2s or -a3s) Align images and save the aligned results
-a1ns   (or -a2ns or -a3ns) Align images without cropping the aligned results, and save the aligned results
-am Maximum shift between the images that will be considered when aligning, as a percentage of image width/height - e.g. -am 20 to allow an image to be shifted by 2010000546320f its size with respect to the others

-t1     Tone map HDR image with "Details Enhancer" (requires option -3)
-t2     Tone map HDR image with "Tone Compressor" (requires option -3)
-x1     Settings file in XMP format for tone mapping method "Details Enhancer", .e.g. -x1 EnhancerSettings.xmp
-x2     Settings file in XMP format for tone mapping method "Tone Compressor", .e.g. -x2 CompressorSettings.xmp

-co     Output color space when source images are RAW files. Options are -co 0 for sRGB, -co 1 for AdobeRGB (default), -co 2 for ProPhotoRGB
-bi     Bit-depth of result image when saving as tif: -bi 8 for 8 bits/channel, -bi 16 for 16/bits channel
-p      Result image is a 360 degree panorama intended to be viewed in a panorama viewer - useful for methods Details Enhancer and Fusion/Natural only.
-ca     Reduce Chromatic Aberattions
-no     Reduce Noise: -no0 to reduce noise in underexposed photos, -no1 for normal and underexposed photos, -no2 for all photos, -no9 for merged image noise reduction
-nr Noise reduction strength for -n0, -n1 and -n2 options - e.g. -ns 100 (valid values vary between 50 and 150)
-tr     Consider files with the .tif extension coming from the Canon 1Ds and Phase One P45 as RAW files

-d      Destination path for the resulting image, including the last "\" character if applicable. E.g. -d C:\images\
-n      Naming options for the resulting images. Options are -n 0 for resulting name starting with name of first image in the set (default), -n 1 for name starting with set number, -n 2 for shortened version starting with set number, -n 3 for name ending with set number and -n 4 for shortened version ending with set number. In the case of 1, 2, 3 or 4 set number has to be given with argument -q, e.g. -q 3
-ns     Naming options for the resulting images (same as above) and appended suffix. E.g. -ns 0 trial1.
-o      Name of the resulting image (without the extension)
-s      Resulting image saved in a format different from the one of the source images. Options are: -s tif or -s jpg
-j      Jpeg quality for resulting image saved in the JPEG format, e.g. -j 90 (default is 100)
-k  Tag saved files with this keyword
-ro     Resize resulting image to the specified width and height e.g. -ro 1024 768 to resize the image to 1024x768


-sa Sharpening Amount, e.g. -sa 50 (valid values vary between 0 and 150)
-sr Sharpening Radius (only works if -sa is greater than 0), e.g. -sr 0.9 (valid values vary between 0.5 and 3)
-st Sharpening Threshold (only works if -sa is greater than 0), e.g. -st 2 (valid values vary between 0 and 20)
-ch Contrast adjustment for highlights, e.g. -ch 10 (valid values vary between 0 and 100)
-cl Contrast adjustment for lights, e.g. -cl 10 (valid values vary between 0 and 100)
-cd Contrast adjustment for darks, e.g. -cd 10 (valid values vary between 0 and 100)
-ch Contrast adjustment for shadows, e.g. -cs 10 (valid values vary between 0 and 100)
