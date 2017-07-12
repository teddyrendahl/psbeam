"""
Init file for images that can be used as templates or stock images in psbeam.
"""
############
# Standard #
############
import os

###############
# Third Party #
###############
import cv2

##########
# Module #
##########
from ..utils.cvutils import get_images_from_dir

# Grab all the images in this directory
test_images = get_images_from_dir(
    os.path.dirname(os.path.abspath(__file__)), out_type=dict)
