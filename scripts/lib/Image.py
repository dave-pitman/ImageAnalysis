#!/usr/bin/python3

# Image.py - manage all the data scructures associated with an image

import pickle
import cv2
import json
import math
#from matplotlib import pyplot as plt
import navpy
import numpy as np
import os.path
import sys

from props import getNode

from . import transformations


d2r = math.pi / 180.0           # a helpful constant
    
class Image():
    def __init__(self, meta_dir=None, image_base=None):
        if image_base != None:
            self.name = image_base
            self.node = getNode("/images/" + self.name, True)
        else:
            self.name = None
        #self.img = None
        #self.img_rgb = None
        self.kp_list = []       # opencv keypoint list
        self.kp_usage = []
        self.des_list = None      # opencv descriptor list
        self.match_list = {}

        self.uv_list = []       # the 'undistorted' uv coordinates of all kp's
        
        # cam2body/body2cam are transforms to map between the standard
        # lens coordinate system (at zero roll/pitch/yaw and the
        # standard ned coordinate system at zero roll/pitch/yaw).
        # cam2body is essentially a +90 pitch followed by +90 roll (or
        # equivalently a +90 yaw followed by +90 pitch.)  This
        # transform simply maps coordinate systems and has nothing to
        # do with camera mounting offset or pose or anything other
        # than converting from one system to another.
        self.cam2body = np.array( [[0, 0, 1],
                                   [1, 0, 0],
                                   [0, 1, 0]],
                                  dtype=float )
        self.body2cam = np.linalg.inv(self.cam2body)

        # fixme: num_matches and connections appear to be the same
        # idea computed and used in different places.  We should be
        # able to collapse this into a single consistent value.
        self.num_matches = 0
        self.connections = 0.0
        self.cycle_depth = -1
        self.connection_order = -1
        self.weight = 1.0

        self.error = 0.0
        self.stddev = 0.0
        self.placed = False

        self.coord_list = []
        self.corner_list = []
        self.grid_list = []

        self.center = []
        self.radius = 0.0

        if image_base:
            dir_node = getNode('/config/directories', True)
            self.image_file = None
            for i in range( dir_node.getLen('image_sources') ):
                dir = os.path.normpath(dir_node.getStringEnum('image_sources', i))
                tmp1 = os.path.join(dir, image_base + '.JPG')
                tmp2 = os.path.join(dir, image_base + '.jpg')
                if os.path.isfile(tmp1):
                    self.image_file = tmp1
                elif os.path.isfile(tmp2):
                    self.image_file = tmp2
            if not self.image_file:
                print('Warning: no image source file found:', image_base)
                self.image_file = None
            file_root = os.path.join(meta_dir, image_base)
            self.features_file = file_root + ".feat"
            self.des_file = file_root + ".desc"
            self.match_file = file_root + ".match"
            
    def load_rgb(self, equalize=False):
        # print("Loading:", self.image_file)
        try:
            img_rgb = cv2.imread(self.image_file, flags=cv2.IMREAD_ANYCOLOR|cv2.IMREAD_ANYDEPTH|cv2.IMREAD_IGNORE_ORIENTATION)
            if equalize:
                # equalize val (essentially gray scale level)
                clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
                hsv = cv2.cvtColor(img_rgb, cv2.COLOR_BGR2HSV)
                hue, sat, val = cv2.split(hsv)
                aeq = clahe.apply(val)
                # recombine
                hsv = cv2.merge((hue,sat,aeq))
                # convert back to rgb
                img_rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            h, w = img_rgb.shape[:2]
            self.node.setInt('height', h)
            self.node.setInt('width', w)
            return img_rgb

        except:
            print(self.image_file + ":\n" + "  rgb load error: " \
                + str(sys.exc_info()[1]))
            return None

    def load_gray(self):
        #print "Loading " + self.image_file
        try:
            rgb = self.load_rgb()
            gray = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
            # adaptive histogram equilization (block by block)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            aeq = clahe.apply(gray)
            #cv2.imshow('adaptive history equalization', aeq)
            return aeq
        except:
            print(self.image_file + ":\n" + "  gray load error: " \
                + str(sys.exc_info()[1]))

    def get_size(self):
        return self.node.getInt('width'), self.node.getInt('height')
    
    def load_features(self):
        if len(self.kp_list) == 0 and os.path.exists(self.features_file):
            #print "Loading " + self.features_file
            try:
                feature_list = pickle.load( open( self.features_file, "rb" ) )
                for point in feature_list:
                    kp = cv2.KeyPoint(x=point[0][0], y=point[0][1],
                                      _size=point[1], _angle=point[2],
                                      _response=point[3], _octave=point[4],
                                      _class_id=point[5])
                    self.kp_list.append(kp)
            except:
                # unpickle failed, try old style json
                try:
                    f = open(self.features_file, 'r')
                    feature_dict = json.load(f)
                    f.close()
                except:
                    print(self.features_file + ":\n" + "  feature load error: " \
                        + str(sys.exc_info()[0]) + ": " + str(sys.exc_info()[1]))
                    return

                feature_list = feature_dict['features']
                for i, kp_dict in enumerate(feature_list):
                    angle = kp_dict['angle']
                    class_id = kp_dict['class-id']
                    octave = kp_dict['octave']
                    pt = kp_dict['pt']
                    response = kp_dict['response']
                    size = kp_dict['size']
                    self.kp_list.append( cv2.KeyPoint(pt[0], pt[1], size,
                                                      angle, response, octave,
                                                      class_id) )

    def load_descriptors(self):
        filename = self.des_file + ".npy"
        if os.path.exists(filename):
            if self.des_list is None:
                #print "Loading " + filename
                try:
                    self.des_list = np.load(filename)
                    #print np.any(self.des_list)
                    #val = "%s" % self.des_list
                    #print
                    #print "des_list.size =", self.des_list.size
                    #print val
                    #print
                except:
                    print(filename + ":\n" + "  desc load error: " \
                        + str(sys.exc_info()[1]))
        else:
            print("no file:", filename)
            
    def load_matches(self):
        try:
            self.match_list = pickle.load( open( self.match_file, "rb" ) )
            #print(self.match_list)
        except:
            print(self.match_file + ":\n" + "  matches load error: " \
                  + str(sys.exc_info()[0]) + ": " + str(sys.exc_info()[1]))
            return

    def save_features(self):
        # convert from native opencv kp class to a python list
        feature_list = []
        for kp in self.kp_list:
            point = (kp.pt, kp.size, kp.angle, kp.response, kp.octave,
                     kp.class_id)
            feature_list.append(point)
        try:
            pickle.dump(feature_list, open(self.features_file, "wb"))
        except IOError as e:
            print("save_features(): I/O error({0}): {1}".format(e.errno, e.strerror))
            return
        except:
            raise

    def save_descriptors(self):
        # write descriptors as 'ppm image' format
        try:
            result = np.save(self.des_file, self.des_list)
        except:
            print(self.des_file + ": error saving file: " \
                + str(sys.exc_info()[1]))

    def save_matches(self):
        try:
            pickle.dump(self.match_list, open(self.match_file, "wb"))
        except IOError as e:
            print(self.match_file + ": error saving file: " \
                + str(sys.exc_info()[1]))
            return
        except:
            raise

    def make_detector(self):
        detector_node = getNode('/config/detector', True)
        detector = None
        if detector_node.getString('detector') == 'SIFT':
            max_features = detector_node.getInt('sift_max_features')
            detector = cv2.xfeatures2d.SIFT_create(nfeatures=max_features)
        elif detector_node.getString('detector') == 'SURF':
            threshold = detector_node.getFloat('surf_hessian_threshold')
            nOctaves = detector_node.getInt('surf_noctaves')
            detector = cv2.xfeatures2d.SURF_create(hessianThreshold=threshold, nOctaves=nOctaves)
        elif detector_node.getString('detector') == 'ORB':
            max_features = detector_node.getInt('orb_max_features')
            grid_size = detector_node.getInt('grid_detect')
            cells = grid_size * grid_size
            max_cell_features = int(max_features / cells)
            detector = cv2.ORB_create(max_cell_features)
        elif detector_node.getString('detector') == 'Star':
            maxSize = detector_node.getInt('star_max_size')
            responseThreshold = detector_node.getInt('star_response_threshold')
            lineThresholdProjected = detector_node.getInt('star_line_threshold_projected')
            lineThresholdBinarized = detector_node.getInt('star_line_threshold_binarized')
            suppressNonmaxSize = detector_node.getInt('star_suppress_nonmax_size')
            detector = cv2.xfeatures2d.StarDetector_create(maxSize, responseThreshold,
                                        lineThresholdProjected,
                                        lineThresholdBinarized,
                                        suppressNonmaxSize)
        return detector

    def orb_grid_detect(self, detector, image, grid_size):
        steps = grid_size
        kp_list = []
        h, w = image.shape
        dx = 1.0 / float(steps)
        dy = 1.0 / float(steps)
        x = 0.0
        for i in xrange(steps):
            y = 0.0
            for j in xrange(steps):
                #print "create mask (%dx%d) %d %d" % (w, h, i, j)
                #print "  roi = %.2f,%.2f %.2f,%2f" % (y*h,(y+dy)*h-1, x*w,(x+dx)*w-1)
                mask = np.zeros((h,w,1), np.uint8)
                mask[y*h:(y+dy)*h-1,x*w:(x+dx)*w-1] = 255
                kps = detector.detect(image, mask)
                kp_list.extend( kps )
                y += dy
            x += dx
        return kp_list

    def detect_features(self, img, scale):
        # scale image for feature detection.  Note that with feature
        # detection, often less is more ... scaling to a smaller image
        # can allow the feature detector to see bigger scale features.
        # With outdoor natural images at full detail, oftenthe
        # detector/matcher gets lots in the microscopic details and
        # sees more noise than valid features.
        scaled = cv2.resize(img, (0,0), fx=scale, fy=scale)
        
        detector_node = getNode('/config/detector', True)
        detector = self.make_detector()
        grid_size = detector_node.getInt('grid_detect')
        if detector_node.getString('detector') == 'ORB' and grid_size > 1:
            kp_list = self.orb_grid_detect(detector, scaled, grid_size)
        else:
            kp_list = detector.detect(scaled)

        # compute the descriptors for the found features (Note: Star
        # is a special case that uses the brief extractor
        #
        # compute() could potential add/remove keypoints so we want to
        # save the returned keypoint list, not our original detected
        # keypoint list
        if detector_node.getString('detector') == 'Star':
            extractor = cv2.DescriptorExtractor_create('ORB')
        else:
            extractor = detector
        self.kp_list, self.des_list = extractor.compute(scaled, kp_list)

        # scale the keypoint coordinates back to the original image size
        for kp in self.kp_list:
            #print('scaled:', kp.pt, ' ', end='')
            kp.pt = (kp.pt[0]/scale, kp.pt[1]/scale)
            #print('full:', kp.pt)
            
        # wipe matches because we've touched the keypoints
        self.match_list = {}

    # Displays the image in a window and waits for a keystroke and
    # then destroys the window.  Returns the value of the keystroke.
    def show_features(self, flags=0):
        # flags=0: draw only keypoints location
        # flags=4: draw rich keypoints
        rgb = self.load_rgb(equalize=True)
        w, h = self.get_size()
        scale = 1000.0 / float(h)
        kp_list = []
        for kp in self.kp_list:
            angle = kp.angle
            class_id = kp.class_id
            octave = kp.octave
            pt = kp.pt
            response = kp.response
            size = kp.size
            x = pt[0] * scale
            y = pt[1] * scale
            kp_list.append( cv2.KeyPoint(x, y, size, angle, response,
                                         octave, class_id) )

        scaled_image = cv2.resize(rgb, (0,0), fx=scale, fy=scale)
        #res = cv2.drawKeypoints(scaled_image, kp_list, None,
        #                        color=(0,255,0), flags=flags)
        for kp in kp_list:
            cv2.circle(scaled_image, (int(kp.pt[0]), int(kp.pt[1])), 3, (0,255,0), 1, cv2.LINE_AA)
            
        cv2.imshow(self.name, scaled_image)
        print('waiting for keyboard input...')
        key = cv2.waitKey() & 0xff
        cv2.destroyWindow(self.name)
        return key

    def coverage_xy(self):
        if not len(self.corner_list_xy):
            return (0.0, 0.0, 0.0, 0.0)

        # find the min/max area of the image
        p0 = self.corner_list_xy[0]
        xmin = p0[0]; xmax = p0[0]; ymin = p0[1]; ymax = p0[1]
        for pt in self.corner_list_xy:
            if pt[0] < xmin:
                xmin = pt[0]
            if pt[0] > xmax:
                xmax = pt[0]
            if pt[1] < ymin:
                ymin = pt[1]
            if pt[1] > ymax:
                ymax = pt[1]
        #print "%s coverage: (%.2f %.2f) (%.2f %.2f)" \
        #    % (self.name, xmin, ymin, xmax, ymax)
        return (xmin, ymin, xmax, ymax)
    
    def coverage_lla(self, ref):
        xmin, ymin, xmax, ymax = self.coverage_xy()
        minlla = navpy.ned2lla([ymin, xmin, 0.0], ref[0], ref[1], ref[2])
        maxlla = navpy.ned2lla([ymax, xmax, 0.0], ref[0], ref[1], ref[2])
        return(minlla[1], minlla[0], maxlla[1], maxlla[0])

    def ypr_to_quat(self, yaw_deg, pitch_deg, roll_deg):
        quat = transformations.quaternion_from_euler(yaw_deg * d2r,
                                                     pitch_deg * d2r,
                                                     roll_deg * d2r,
                                                     'rzyx')
        return quat

    def set_aircraft_pose(self,
                          lat_deg, lon_deg, alt_m,
                          yaw_deg, pitch_deg, roll_deg,
                          flight_time=-1.0):
        # computed from euler angles
        quat = self.ypr_to_quat(yaw_deg, pitch_deg, roll_deg)
        ac_pose_node = self.node.getChild('aircraft_pose', True)
        ac_pose_node.setFloat('lat_deg', lat_deg)
        ac_pose_node.setFloat('lon_deg', lon_deg)
        ac_pose_node.setFloat('alt_m', alt_m)
        ac_pose_node.setFloat('yaw_deg', yaw_deg)
        ac_pose_node.setFloat('pitch_deg', pitch_deg)
        ac_pose_node.setFloat('roll_deg', roll_deg)
        ac_pose_node.setLen('quat', 4)
        for i in range(4):
            ac_pose_node.setFloatEnum('quat', i, quat[i])
        if flight_time > 0.0:
            self.node.setFloat("flight_time", flight_time)
            
    # ned = [n_m, e_m, d_m] relative to the project ned reference point
    # ypr = [yaw_deg, pitch_deg, roll_deg] in the ned coordinate frame
    # note that the matrix derived from 'quat' is inv(R) is transpose(R)
    def set_camera_pose(self, ned, yaw_deg, pitch_deg, roll_deg, opt=False):
        # computed from euler angles
        quat = self.ypr_to_quat(yaw_deg, pitch_deg, roll_deg)
        if opt:
            cam_pose_node = self.node.getChild('camera_pose_opt', True)
            cam_pose_node.setBool('valid', True)
        else:
            cam_pose_node = self.node.getChild('camera_pose', True)
        for i in range(3):
            cam_pose_node.setFloatEnum('ned', i, ned[i])
        cam_pose_node.setFloat('yaw_deg', yaw_deg)
        cam_pose_node.setFloat('pitch_deg', pitch_deg)
        cam_pose_node.setFloat('roll_deg', roll_deg)
        cam_pose_node.setLen('quat', 4)
        for i in range(4):
            cam_pose_node.setFloatEnum('quat', i, quat[i])
        #cam_pose_node.pretty_print('  ')
        
    # set the camera pose using rvec, tvec (rodrigues) which is the
    # output of certain cv2 functions like solvePnP()
    def rvec_to_body2ned(self, rvec):
        # print "rvec=", rvec
        Rned2cam, jac = cv2.Rodrigues(rvec)

        # Our Rcam matrix (in our ned coordinate system) is body2cam * Rned,
        # so solvePnP returns this combination.  We can extract Rned by
        # premultiplying by cam2body aka inv(body2cam).
        cam2body = self.get_cam2body()
        Rned2body = cam2body.dot(Rned2cam)
        Rbody2ned = np.matrix(Rned2body).T
        return Rbody2ned

    def get_aircraft_pose(self):
        pose_node = self.node.getChild('aircraft_pose', True)
        lla = [ pose_node.getFloat('lat_deg'),
                pose_node.getFloat('lon_deg'),
                pose_node.getFloat('alt_m') ]
        ypr = [ pose_node.getFloat('yaw_deg'),
                pose_node.getFloat('pitch_deg'),
                pose_node.getFloat('roll_deg') ]
        quat = []
        for i in range(4):
            quat.append( pose_node.getFloatEnum('quat', i) )
        return lla, ypr, quat

    def get_camera_pose(self, opt=False):
        if opt:
            pose_node = self.node.getChild('camera_pose_opt', True)
        else:
            pose_node = self.node.getChild('camera_pose', True)
        ned = []
        for i in range(3):
            ned.append( pose_node.getFloatEnum('ned', i) )
        ypr = [ pose_node.getFloat('yaw_deg'),
                pose_node.getFloat('pitch_deg'),
                pose_node.getFloat('roll_deg') ]
        quat = []
        for i in range(4):
            quat.append( pose_node.getFloatEnum('quat', i) )
        return ned, ypr, quat

    # cam2body rotation matrix (M)
    def get_cam2body(self):
        return self.cam2body

    # body2cam rotation matrix (IM)
    def get_body2cam(self):
        return self.body2cam

    # ned2body (R) rotation matrix
    def get_ned2body(self, opt=False):
        return np.matrix(self.get_body2ned(opt)).T
    
   # body2ned (IR) rotation matrix
    def get_body2ned(self, opt=False):
        ned, ypr, quat = self.get_camera_pose(opt)
        return transformations.quaternion_matrix(np.array(quat))[:3,:3]

    # compute rvec and tvec (used to build the camera projection
    # matrix for things like cv2.triangulatePoints) from camera pose
    def get_proj(self, opt=False):
        body2cam = self.get_body2cam()
        ned2body = self.get_ned2body(opt)
        R = body2cam.dot( ned2body )
        rvec, jac = cv2.Rodrigues(R)
        ned, ypr, quat = self.get_camera_pose(opt)
        tvec = -np.matrix(R) * np.matrix(ned).T
        return rvec, tvec
