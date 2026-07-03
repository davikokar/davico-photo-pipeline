# TODOs

TODO: write a schematic activity diagram of the pipeline, showing the steps and the possible paths between them.

tODO: should add a step "raw conversion check" after the raw conversion step, in order to check if the raw conversion 
was successful. This step should check if the planned images are actually present, if they are ok, if some are missing, then
the conversion step should be rerun for the missing or failed images. Now the problem is how to obtain the information about
which images should be present after the raw conversion step. We can save this information in the output json file as soon as the planned conversions
 are available, or maybe they can be read from the state.json, have to verify that. In any case the conversion is group-based, so
if an image is missing we must delete the whole group and rerun the conversion for the whole group, in order to maintain the consistency of the data.
 

TODO:
- check the photomatix settings in pipeline, there are some that are outdated.
- in general: the json creation should be incremental, every step should add its own section to the 
group json file, and then save the file with a new name, so that we have a history of the changes and we can 
also easily revert to a previous step if something goes wrong.
- add a flag that would allow to skip the raw conversion with recipe 0, because we already have the jpg image
- maybe the ghost detection should be done after the hdr merge, because anyway the ghost detection just creates the mask
that has to be used after the hdr merge, in order to merge the "normal" hdr merge with the "noghost" hdr merge. 
So maybe we can do the ghost detection mask creation and hdr result merging in the same step.


This is how the current photomatix calls are generated:


[DEBUG   ] 13:49:36  PhotomatixCL cmd: C:\Program Files\Photomatix\PhotomatixCL.exe -a2 -ca -no1 -md -n 0 -d C:\Temp\pipeline_tests\output\20260511_114848\merged_hdrs\group_004\ -2 -2a 5.0 -2b -6.0 -2c 2.0 -2h 5.0 -2k 6.0 -2m 2.0 -2s 6.0 -2w 6.0 C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870.jpg C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870_-2.jpg C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870_+2.jpg

[DEBUG   ] 13:49:36  PhotomatixCL cmd: C:\Program Files\Photomatix\PhotomatixCL.exe -a2 -ca -no1 -md -n 0 -d C:\Temp\pipeline_tests\output\20260511_114848\merged_hdrs\group_004\ -5 -5a 0.0 -5c 0.0 -5h 2.0 C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870.jpg C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870_-2.jpg C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870_+2.jpg

[DEBUG   ] 13:49:36  PhotomatixCL cmd: C:\Program Files\Photomatix\PhotomatixCL.exe -a2 -ca -no1 -md -n 0 -d C:\Temp\pipeline_tests\output\20260511_114848\merged_hdrs\group_004\ -3 -t2 -x2 C:\Users\seddiod\AppData\Local\Temp\test_photographic.xmp C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870.jpg C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870_-2.jpg C:\Temp\pipeline_tests\output\20260511_114848\raw_to_jpg\0H8A4870_+2.jpg



grouping -> 
human review -> 
if (terrestrial and raw images available) : raw conversion ->
raw conversion check ->  
if (hdr groups available, terrestrial and raw images available) :



if (hdr groups available and ghost mapping available) :
  hdr merge with ghost mapping -> 
if (hdr groups available and no ghost mapping) : 
  hdr merge simple with autoalignment ->

color correction -> 
panorama stitching

possible situations:
- aerial, hdr
- aerial, singleshot
- terrestrial, hdr, with raw
- terrestrial, hdr, without raw
- terrestrial, singleshot, with raw
- terrestrial, singleshot, without raw
(all these can be with or without panoramas)

full pipeline:
-grouping
-human review
-raw conversion
-align
-ghost detection
-hdr merge
-ghost application


# Command examples:

## Process a folder
python run.py process ./foto_sessione_roma/

## Resume an interrupted session
python run.py resume ./workspace/session_20250306/

## Reprocess only a step of a group
python run.py rerun --session 20250306 --group group_001 --step color

## Show session state
python run.py status ./workspace/session_20250306/


# How to setup dev environment

## Create a python environment:
- python -m venv .venv


## Activate it:
- source .venv/scripts/activate


## Install uv, create toml and sync:
- pip install uv
- create file pyproject.toml
- run "uv sync --active"


# External Tools

## Photomatix

### Command example for Realistic:
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -5 -5a 0.0 -5c 0.0 -5h 2.0 "C:\temp\pipeline_tests\0H8A4390.JPG" "C:\temp\pipeline_tests\0H8A4391.JPG" "C:\temp\pipeline_tests\0H8A4392.JPG"

### Command example for Photographic:
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -3 -t2 -x2 "C:\temp\pipeline_tests\photographic.xmp" "C:\temp\pipeline_tests\0H8A4390.JPG" "C:\temp\pipeline_tests\0H8A4391.JPG" "C:\temp\pipeline_tests\0H8A4392.JPG"

### Command example for Adjusted:
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -2 -2a 5.0 -2b -6.0 -2c 2.0 -2h 5.0 -2k 6.0 -2m 2.0 -2s 6.0 -2w 6.0 "C:\temp\pipeline_tests\0H8A4390_s.JPG" "C:\temp\pipeline_tests\0H8A4391_s.JPG" "C:\temp\pipeline_tests\0H8A4392_s.JPG"

## Rawtherapee

### Location
C:\Program Files\RawTherapee\5.12\rawtherapee-cli.exe

### Command example to convert raw files to jpg with different exposures (-2, 0, +2)
rawtherapee-cli -p "C:\temp\pipeline_tests\rawtherapee_profile_exposure_0.pp3" -o "C:\temp\pipeline_tests\raw\0H8A4482_0.jpg" -c "C:\temp\pipeline_tests\raw\0H8A4482.CR3"
rawtherapee-cli -p "C:\temp\pipeline_tests\rawtherapee_profile_exposure_-2.pp3" -o "C:\temp\pipeline_tests\raw\0H8A4482_-2.jpg" -c "C:\temp\pipeline_tests\raw\0H8A4482.CR3"
rawtherapee-cli -p "C:\temp\pipeline_tests\rawtherapee_profile_exposure_+2.pp3" -o "C:\temp\pipeline_tests\raw\0H8A4482_+2.jpg" -c "C:\temp\pipeline_tests\raw\0H8A4482.CR3"