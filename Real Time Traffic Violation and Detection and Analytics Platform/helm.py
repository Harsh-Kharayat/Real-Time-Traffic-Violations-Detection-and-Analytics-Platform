from time import sleep
import cv2 as cv
import argparse
import sys
import numpy as np
import os.path
from glob import glob
#from PIL import image
frame_count = 0             # used in mainloop  where we're extracting images., and then to drawPred( called by post process)
frame_count_out=0           # used in post process loop, to get the no of specified class value.
# Initialize the parameters 
confThreshold = 0.5  #Confidence threshold
nmsThreshold = 0.4   #Non-maximum suppression threshold
inpWidth = 416       #Width of network's input image
inpHeight = 416      #Height of network's input image


# Load names of classes
classesFile = "obj.names";
classes = None
with open(classesFile, 'rt') as f:
    classes = f.read().rstrip('\n').split('\n')

# Give the configuration and weight files for the model and load the network using them.
modelConfiguration = "yolov3-obj.cfg";
modelWeights = "yolov3-obj_2400.weights";

net = cv.dnn.readNetFromDarknet(modelConfiguration, modelWeights)
net.setPreferableBackend(cv.dnn.DNN_BACKEND_OPENCV)
net.setPreferableTarget(cv.dnn.DNN_TARGET_CPU)

# Get the names of the output layers
def getOutputsNames(net):
    layersNames = net.getLayerNames()
    outLayers = net.getUnconnectedOutLayers()
    # Convert to flat list of ints
    outLayers = outLayers.flatten() if hasattr(outLayers, "flatten") else outLayers
    return [layersNames[i - 1] for i in outLayers]

# Draw the predicted bounding box
def drawPred(classId, conf, left, top, right, bottom, frame):

    global frame_count
# Draw a bounding box.
    #cv.rectangle(frame, (left, top), (right, bottom), (255, 178, 50), 3)
    label = '%.2f' % conf
    # Get the label for the class name and its confidence
    if classes:
        assert(classId < len(classes))
        label = '%s:%s' % (classes[classId], label)

    #Display the label at the top of the bounding box
    labelSize, baseLine = cv.getTextSize(label, cv.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    top = max(top, labelSize[1])


    label_name,label_conf = label.split(':')    #spliting into class & confidance. will compare it with person.
    if label_name == 'Helmet':
        frame_count+=1
        

    #print(frame_count)
    if(frame_count> 0):
        return frame_count


# Remove the bounding boxes with low confidence using non-maxima suppression
def postprocess(frame, outs):
    frameHeight = frame.shape[0]
    frameWidth = frame.shape[1]
    frame_count_out = 0
    classIds = []
    confidences = []
    boxes = []

    count_person = 0  # initialize counter

    # Process network outputs
    for out in outs:
        for detection in out:
            scores = detection[5:]
            classId = np.argmax(scores)
            confidence = scores[classId]
            if confidence > confThreshold:
                center_x = int(detection[0] * frameWidth)
                center_y = int(detection[1] * frameHeight)
                width = int(detection[2] * frameWidth)
                height = int(detection[3] * frameHeight)
                left = int(center_x - width / 2)
                top = int(center_y - height / 2)
                classIds.append(classId)
                confidences.append(float(confidence))
                boxes.append([left, top, width, height])

    # Non-maximum suppression
    indices = cv.dnn.NMSBoxes(boxes, confidences, confThreshold, nmsThreshold)

    # Make indices safe to iterate
    if indices is None:
        indices = []
    elif isinstance(indices, (tuple, list, np.ndarray)):
        indices = np.array(indices).flatten().tolist()  # flatten to a simple list

    for i in indices:
        box = boxes[i]
        left, top, width, height = box
        frame_count_out = drawPred(classIds[i], confidences[i],
                                   left, top, left + width, top + height, frame)

        my_class = 'Helmet'
        unknown_class = classes[classIds[i]]
        if my_class == unknown_class:
            count_person += 1

    return 1 if count_person >= 1 else 0
    

# Process inputs
winName = 'Deep learning object detection in OpenCV'
cv.namedWindow(winName, cv.WINDOW_NORMAL)



def detect(frame):
    #frame = cv.imread(fn)
    frame_count =0

    # Create a 4D blob from a frame.
    blob = cv.dnn.blobFromImage(frame, 1/255, (inpWidth, inpHeight), [0,0,0], 1, crop=False)

    # Sets the input to the network
    net.setInput(blob)

    # Runs the forward pass to get output of the output layers
    outs = net.forward(getOutputsNames(net))

    # Remove the bounding boxes with low confidence

    # Put efficiency information. 
    #The function getPerfProfile returns the overall time for inference(t) and the timings for each of the layers(in layersTimes)
    t, _ = net.getPerfProfile()
    
    label = 'Inference time: %.2f ms' % (t * 1000.0 / cv.getTickFrequency())
    
    #cv.putText(frame, label, (0, 15), cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255))
    
    k=postprocess(frame, outs)
    if k:
        return 1
    else:
        return 0
 
 
 
