"""
Script containing the focus class for the purpose of automatically focusing any
camera that has a machine-tunable focuser. This script assumes that this comes
in the form of a motor that can be contacted via channel access in EPICS.
"""
from __future__ import print_function
from joblib import Memory
from blbase import motor, iterscan
from psp import Pv as pv
from utils.cvUtils import to_uint8
from scipy.optimize import minimize
from collections import Iterable
import cv2
import numpy as np

cachedir = "cache"
mem = Memory(cachedir=cachedir, verbose=0)

################################################################################
#                                  Focus Class                                 #
################################################################################

class Focus(object):
    """
    Base focusing class that will determine the focus over a range of values and
    then return the value that had the highest focus.

    Kwargs:
        resize (float): Resize factor (1.0 keeps image same size).
        kernel (tuple): Tuple of length 2 for gaussian kernel size.
        sigma (int): Gaussian std in x and y. Set 0 to compute internally.

    """
    
    def __init__(self, motor_pv, camera_pv, **kwargs):

        self.motor_pv   = motor_pv
        self.camera_pv  = camera_pv
        self.positions  = kwargs.get("positions", None)
        self.resize     = kwargs.get("resize", 1.0)
        self.kernel     = kwargs.get("kernel", (17,17))
        self.sigma      = kwargs.get("sigma", 0)
        self.average    = kwargs.get("average", 3)
        self.method     = kwargs.get("method", "scan")
        self.sharpness  = kwargs.get("sharpness", "laplacian")
        self.imager     = kwargs.get("imager", "default")
        self.best_pos   = None
        self.best_focus = 0
        self._focus_methods     = {"scan"      : self._scan_focus,
                                   "hillclimb" : self._hillclimb_focus}
        self._sharpness_methods = {"sobel"     : self._sobel_var, 
                                   "laplacian" : self._laplacian_var}
        self._check_arguments()
        self._motors      = self._get_motor_objs()
        self._motor_iters = self._get_motor_iters()

    def _check_arguments(self):
        # TODO: Write exceptions file and rewrite this correctly
        assert isiterable(self.positions)
        assert isiterable(self.motor_pv) or isinstance(
            self.motor_pv, (basestring, Motor, VirtualMotor))
        if isinstance(self.motor_pv, (basestring, Motor)):
            assert_equals(len(self.positions), 3)
        elif isiterable(self.motor_pv):
            assert_equals(len(self.positions), len(self.motor_pv))
            for pv, pos in zip(self.motor_pv, self.positions):
                assert isinstance(pv, basestring)
                assert isiterable(pos)
                assert_equals(len(pos), 3)
        elif isinstance(self.motor_pv, VirtualMotor): 
            assert_equals(len(self.positions), self.motor_pv.num_motors)
            for pos in self.positions:
                assert isiterable(pos)
                assert_equals(len(pos), 3)
        assert isinstance(self.camera_pv, basestring)
        assert self.resize > 0
        assert isinstance(self.kernel, tuple)
        assert_equals(len(self.kernel), 2)
        assert self.sigma >= 0
        assert isinstance(self.average, int)
        assert self.average > 0
        assert self.method in self._focus_methods.keys()
        assert self.sharpness in self._sharpness_methods.keys()
        
    def _get_motor_objs(self):
        motors = []
        if isinstance(self.motor_pv, (Motor, VirtualMotor)):
            return self.motor_pv
        elif isinstance(self.motor_pv, basestring):
            return Motor(self.motor_pv, name=pv.get(self.motor_pv+".DESC"))
        elif isiterable(self.motor_pv):
            return VirtualMotor(self.motor_pv)
        
    def _get_motor_iters(self):
        if not self.positions:
            return None
        pos_list = []
        # This is most likely incorrect. Check this first if things go wrong
        if isiterable(self.positions[0]):
            for pos in self.positions:
                pos_list.append(range(*pos))
            pos_list = zip(*pos_list)
        else:
            pos_list = range(*self.positions)
        return iter(pos_list)

    def preprocess(self, image):
        """Preprocess the image by resizing and running a gaussian blur. A
        histogram equalization is run on the image as well.

        Args:
            image (np.ndarray): The image to be preprocessed.
        Returns:
            np.ndarray. Preprocessed Image.
    
        Depending on the specific use case this method should be overwritten to
        implement the necessary preprocessing pipeline.
        """
        image = to_uint8(image)
        image_small = cv2.resize(image, (0,0), fx=self.resize, fy=self.resize)
        image_gblur = cv2.GaussianBlur(image_small, self.kernel, self.sigma)
        image_hequ  = cv2.equalizeHist(image_gblur)   #Examine effects
        return image_hequ

    def get_image(self, camera_pv=None):
        if imager.lower() == "default":            
            if camera_pv:
                self.camera_pv = camera_pv
            return pv.get(camera_pv)
        else:
            return self.imager()
        
    def _laplacian_var(self, image):
        return cv2.Laplacian(image, cv2.CV_64F).var()

    def _sobel_var(self, image, ksize=5):
        sobel_x = cv2.Sobel(image, cv2.CV_64F, 1, 0, ksize=ksize).var()
        sobel_y = cv2.Sobel(image, cv2.CV_64F, 0, 1, ksize=ksize).var()
        return sobel_x/2.0 + sobel_y/2.0
    
    def get_focus(self, image, sharpness="laplacian", const=1):
        image_prep = self.preprocess(image)
        return const * self._sharpness_methods[sharpness](image)

    def get_ave_focus(self, sharpness="laplacian", const=1):
        focus = np.empty([self.average])
        for i in range(self.average):
            image = self.get_image()
            focus[i] = self.get_focus(image, sharpness=sharpness, const=const)
        return focus.mean()
    
    def _scan_focus(self):
        assert self._motor_iters, "Motor iterators not initialized"
        scan = IterScan(self, self._motors, self._motor_iters)
        scan.scan_mesh()
        return self.best_pos

    def _move_and_focus(self, position):
        self._motors.mv(position)
        self._motors.wait()
        return self._get_ave_focus(const=-1)

    def _hillclimb_focus(self, method="BFGS"):
        self.best_pos = minimize(self._move_and_focus, self._motors.wm(), 
                                 method=method)
        return self.best_pos
    
    def focus(self, method="scan", sharpness="laplacian"):
        if method != self.method:
            self.method = method
        if sharpness != self.sharpness:
            self.sharpness = sharpness
        return self._focus_methods[self.method]()

    def pre_focus_hook(self, current_image, current_position):
        pass
    
    def post_focus_hook(self, current_image, current_position, current_focus):
        pass

    # Methods required for this class to function as an IterScan hook
    def pre_step(self, scan):
        pass

    def post_step(self, scan):
        self._current_pos = self.positions.next()
        self.pre_focus_hook(image, current_pos)
        focus = get_ave_focus()
        if focus > self.best_focus:
            self.best_focus = focus
            self.best_pos = self._current_pos
        self.post_focus_hook(image, self._current_pos, focus)

    def pre_scan(self, scan):
        pass

    def post_scan(self, scan):
        print("Scan completed. \nBest focus found at: {0}".format(
            self.best_pos))

    # TODO: Add all getters and setters

################################################################################
#                        Placeholder Vitual Motor Class                        #
################################################################################

class VirtualMotor(object):
    """Virtual motor class until the real one works."""
    def __init__(self, motors):
        self._motor_pvs = motors
        self._motors    = self._get_motors(self._motor_pvs)
        self.num_motors = len(self._motors)
        self.name       = ""
        for motor in self._motors:
            self.name += motor.name + "+"
        self.name = self.name[:-1]

    def _get_motors(self, motor_pvs):
        motor_names = [pv.get(motor_pv + ".DESC") for motor_pv in motor_pvs]
        return [Motor(motor, name=motor_name) for motor, motor_name in zip(
            motor_pvs, motor_names)]

    def mv(self, vals):
        if len(val) == self.num_motors:
            for motor, val in zip(self._motors, vals):
                motor.mv(val)
        else:
            raise ValueError("Motor and position mismatch: {0} motors with {1} \
inputted motions.".format(len(self._motors), len(vals)))

    def wm(self):
        return [motor.wm() for motor in self._motors]

    def wait(self):
        for motor in self._motors:
            motor.wait()

class VirtualCamera(object):
    """Virtual camera class until one is found/implemented."""
    def __init__(self, camera_pv):
        self.pv = camera_pv
    def get(self):
        return pv.get(self.pv)

def isiterable(obj):
    """
    Function that determines if an object is an iterable, but not including 
    strings.
    """
    if isinstance(obj, basestring):
        return False
    else:
        return isinstance(obj, Iterable)

if __name__ == "__main__":
	pass
