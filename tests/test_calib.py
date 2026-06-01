#!/usr/bin/env python3
"""
Headless validation of calibrate.py geometry on synthetic ground truth (no GUI, no real
images). Confirms: (1) intrinsics recovered from projected checkerboard corners, and
(2) extrinsic pose recovered by solvePnP from projected control points.
"""
import sys
from pathlib import Path
import numpy as np
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from calibrate import (board_object_points, calibrate_intrinsics_from_points,
                       solve_extrinsic, reprojection_error)

K_TRUE = np.array([[800., 0., 320.], [0., 800., 240.], [0., 0., 1.]])
SIZE = (640, 480)
ok_all = True

def check(name, ok, extra=""):
    global ok_all; ok_all = ok_all and ok
    print(("PASS" if ok else "FAIL"), name, extra)

# ---- intrinsics ----
cols, rows, sq = 9, 6, 0.025
objp = board_object_points(cols, rows, sq)
objp = objp - objp.mean(axis=0)            # center the board
rng = np.random.default_rng(0)
objpoints, imgpoints = [], []
for _ in range(18):
    rvec = rng.normal(0, 0.15, 3).astype(np.float64)
    tvec = np.array([rng.normal(0, 0.03), rng.normal(0, 0.03), rng.uniform(0.5, 0.8)])
    proj, _ = cv2.projectPoints(objp, rvec, tvec, K_TRUE, np.zeros(5))
    objpoints.append(objp.astype(np.float32)); imgpoints.append(proj.astype(np.float32))
K, dist, rms = calibrate_intrinsics_from_points(objpoints, imgpoints, SIZE)
fx_err = abs(K[0, 0] - 800) / 800
c_err = np.hypot(K[0, 2] - 320, K[1, 2] - 240)
check("intrinsics fx within 2%", fx_err < 0.02, f"fx={K[0,0]:.1f}")
check("intrinsics principal point within 3 px", c_err < 3, f"err={c_err:.2f}px")
check("intrinsics rms small", rms < 0.5, f"rms={rms:.3f}px")

# ---- extrinsics from control points ----
world = np.array([[0,0,0],[0.3,0,0],[0,0.3,0],[0.3,0.3,0],
                  [0,0,0.3],[0.3,0,0.3],[0,0.3,0.3],[0.3,0.3,0.3]], np.float32)
rvec_t = np.array([0.1, -0.2, 0.05]); tvec_t = np.array([-0.15, -0.1, 1.2])
img, _ = cv2.projectPoints(world, rvec_t, tvec_t, K_TRUE, np.zeros(5))
R, t, rvec, tvec, err = solve_extrinsic(world, img.reshape(-1, 2), K_TRUE, np.zeros(5))
t_err = float(np.linalg.norm(tvec.ravel() - tvec_t))
check("extrinsic reproj < 0.5 px", err < 0.5, f"reproj={err:.4f}px")
check("extrinsic translation recovered", t_err < 1e-2, f"t_err={t_err:.4f} m")

sys.exit(0 if ok_all else 1)
