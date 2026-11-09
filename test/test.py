from numpy import ptp
import numpy as np



points = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]])
principal_axis = ptp(points, axis=0).argmax()
points = points[points[:, principal_axis].argsort(), :]
print(points)