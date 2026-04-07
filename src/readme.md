
# Tool description

This tool automates the processing of photography post processing. The post processing is organized
in subsequent steps. Some steps will require user approval. This is the list of steps:

## Grouping

In the first step a folder containing jpg images is processed in order to group the images according
to this logic.
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


## Raw Conversion to jgp

The second step is the conversion of raw files to jpg. This step can be skipped for aerial DJI images, it is necessary only for Canon images, and only if raw images are available.
The logic is as follows:
- For images that are not part of a hdr group: the raw file is converted to jpg 3 times, with 3 different recipes: the 0 Exp recipe, the -2 Exp recipe, the +2 Exp recipe, in order to obtain 3 exposures of the same image.
- For images that are part of a hdr group: all images are converted with the 0 Exp recipe. The 0 Exp image, is also converted with the other recipes: -2 Exp and + 2 Exp, in case there are three images. The +2 Exp and the -2 Exp images are also converted with, respectively, the -2 Exp and the + 2 Exp recipe.

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
      "ref_image": "0H8A4495.JPG",
      "brackets": [
        {
          "hdr": [
            {
              "filename": "0H8A4495.JPG",
              "ev": 13.88,
              "shutter": 0.008
            },
            {
              "filename": "0H8A4496.JPG",
              "ev": 15.88,
              "shutter": 0.002
            },
            {
              "filename": "0H8A4497.JPG",
              "ev": 11.92,
              "shutter": 0.0125
            }
          ],
          "noghost": [
            {
              "filename": "0H8A4495.JPG",
            },
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

## Hdr Merge

This step is applied only to hdr groups. If there are not hdr groups this step is skipped.

Here there are two different scenarios, depending if there were raw images available or not. The simple case is there were not raw images and the only available images are the bracketed shots.

The images converted with Recipe 0, they are the "normal" images and will be used to generate the hdr image with the best dynamic range, but with the worst ghosting. The images converted from the Exposure 0 (IMG_01.CR2 in the example here above) using all relevant Recipes, will be used to generate an hdr merge with the worst dynamic range (because it is obtained from a single raw), but the best ghosting (no ghosts, since one image is used).



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