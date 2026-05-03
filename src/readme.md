
# Tool description

This tool automates the processing of photography post processing. The post processing 
is organized in subsequent steps. Some steps will require user approval. This is the list 
of steps:

TODO: write a schematic activity diagram of the pipeline, showing the steps and the possible paths between them.

## Grouping

In the first step a folder containing jpg images is processed in order to group the images 
according to this logic.
- Images con be single images.
- Images con be part of a panoramic group (from 2 to n images)
- Images con be part of a hdr group (from 3 to 5)
- Images con be part of a hdr group within a panoramic group

The hdr group should also record the exposure for each image, and determine the central
image (the one with the middle exposure).

The output of the grouping step is a json file with the grouping structure of the images.
This step requires user approval.
The tool generates a web browser page to help the user review and adjust the grouping.
Once the grouping is confirmed the user can save it and place it in the session folder.

TODO: we need to add a an attribute to each group, specifying if the group is an aerial image or a terrestrial image.
This is important because for aerial images we don't have raw files, and we don't have the possibility to generate 
exposure-normalized images, so we need to skip some steps of the pipeline for aerial hdr groups. We can try to automatically
determine if a group is aerial or terrestrial based on the metadata of the images (make "canon" -> "terrestrial", make "dji" -> "aerial"), 
but we need to provide the user with the possibility to adjust it in case of wrong automatic classification.

TODO: we need to improve the panoramic grouping, right now it works well for aerial images, but for terrestrial images 
it is not very accurate. We can try to use the focal length and the gps coordinates to improve the grouping, 
but we need to provide the user with the possibility to adjust it in case of wrong automatic classification.


## Raw Conversion to jgp

The second step is the conversion of raw files to jpg. This step can be skipped for aerial 
DJI images, it is necessary only for Canon images, and only if raw images are available.
The logic is as follows:
- For images that are not part of a hdr group: the raw file is converted to jpg 3 times, 
with 3 different recipes: the 0 Exp recipe, the -2 Exp recipe, the +2 Exp recipe, in order 
to obtain 3 exposures of the same image.
- For images that are part of a hdr group: all images are converted with the 0 Exp recipe. 
The 0 Exp image, is also converted with the other available recipes: for example in case
of a three bracketed hdr sequence these would be the -2 Exp and + 2 Exp recipes. 
The +2 Exp and the -2 Exp images are also converted with, respectively, 
the -2 Exp and the + 2 Exp recipe in order to obtain an exposure-normalized image of
the overexposed and underexposed images.

| Image      | Exposure | Recipe 0 | Recipe -1 | Recipe +1 | Recipe -2 | Recipe +2 | 
|------------|----------|----------|-----------|-----------|-----------|-----------|
| IMG_01.CR2 |    0     |    x     |    x      |    x      |    x      |    x      |
| IMG_02.CR2 |   -1     |    x     |           |    x      |           |           |
| IMG_03.CR2 |   +1     |    x     |    x      |           |           |           |
| IMG_04.CR2 |   -2     |    x     |           |           |           |    x      |
| IMG_05.CR2 |   +2     |    x     |           |           |    x      |           |

In case of 3 images hdr group: 7 images are converted.
In case of 5 images hdr group: 13 images are converted.

A json file with the new file structure is created and saved in the session folder.

...
    {
      "id": "group_003",
      "type": "hdr",
      "brackets": [
        {
          "shots": [
            {
              "filename": "0H8A4495.JPG",
              "ev": 13.88,
              "shutter": 0.008,
              "step_offset": 0,
              "reference_shot": true
            },
            {
              "filename": "0H8A4496.JPG",
              "ev": 15.88,
              "shutter": 0.002,
              "step_offset": -2,
              "reference_shot": false
            },
            {
              "filename": "0H8A4497.JPG",
              "ev": 11.92,
              "shutter": 0.0125,
              "step_offset": 2,
              "reference_shot": false
            }
          ],
          "noghost": [
            {
              "filename": "0H8A4495_+2.JPG",
            },
            {
              "filename": "0H8A4495_-2.JPG",
            }
          ],
          "normalized": [
            {
              "filename": "0H8A4496_-2.JPG",
            },
            {
              "filename": "0H8A4497_+2.JPG",
            }
          ]
        }
      ]
    }
...

## Image alignment and ghost detection

This step is applied only to canon (terrestrial images) hdr groups that have raw images available. 
For aerial hdr groups, and for hdr groups without raw images, this step is skipped. We skip this 
step for aerial hdr groups because they usually taken from such distance to minimize the presence of ghosts,
 and also because we don't have a way to obtain jpg images from raw aerial images. The alignment of aerial images
 can be delegated to the hdr processing step. We skip it for hdr groups without raw images
because in this case we would need a different approach to ghost detection, since the exposure-normalized 
images are not available. In this case we could try to use the original bracketed images, but the alignment
and ghost detection would be less accurate.

TODO: we need to create a ghost mask based on comparison between the original image and the low exposure image, and 
a second ghost mask based on the comparison between the original image and the high exposure image. The final ghost 
mask is the union of the two masks. This way we can detect ghosts that are visible only in the low exposure image, 
and ghosts that are visible only in the high exposure image.

## Hdr Merge

This step is applied only to hdr groups. If there are not hdr groups this step is skipped.

Here there are two different scenarios, depending if the hdr group went through the alignment and ghost detection step or not. 

Case 1) The hdr group went through the alignment and ghost detection step. In this case we have two sets of images to work with: the "normal" images,
 that are the ones converted with Recipe 0, and the "noghost" images, that are the ones converted with the other recipes.
 The "normal" images are used to generate an hdr image with the best dynamic range, but with the worst ghosting.
 The "noghost" images are used to generate an hdr merge with the worst dynamic range (because it is obtained from a single raw), 
 but the best ghosting (no ghosts, since one image is used). In this case we generate two different hdr merges, one with the "normal" 
 images and one with the "noghost" images, and we use the ghost detection map to merge the two, so that the final hdr image has the best dynamic range and minimal ghosting.

Case 2) We only have one set of bracketed shots without the ghost detection map. In this case we can only generate one hdr merge,
using the "normal" images, and we will have to live with the ghosting that is present in the original bracketed shots.

In both cases the hdr merge is generated with Photomatix, using the command line interface. The tool generates the command line for each hdr group.
The default behaviour is to generate 3 different hdr merges for each hdr group, using 3 different Photomatix recipes: the Realistic recipe, the Photographic recipe, and the Adjusted recipe.

Ideally we propose some merges of the 3 different recipe resuls. For example a merge based on 60 percent of photographic and 40 percent of adjusted (done with layers tranparency).
Another interesting functionality could be to detect the sky in the images and to apply a different percentage of merges to sky and non-sky areas.


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