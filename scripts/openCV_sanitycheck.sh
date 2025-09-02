#!/usr/bin/env bash
# Sanity check to make sure OpenCV is working correctly. Should have a little window pop up with a green square in it.
python - <<'PY'
import cv2, numpy as np
img = np.zeros((240,320,3), np.uint8); img[:]=(0,255,0)
cv2.namedWindow("opencv-gui-test", cv2.WINDOW_NORMAL)
cv2.imshow("opencv-gui-test", img)
print("If you see a green window, GUI is OK. It will close in ~5s.")
cv2.waitKey(5000)
cv2.destroyAllWindows()
PY
