#!/usr/bin/env python3
"""
calibrate.py — camera calibration for 3D reconstruction (OpenCap-style), Step 7b.

INTRINSICS  (per camera): printed checkerboard -> focal length, principal point, lens
            distortion via cv2.calibrateCamera over multiple board views.

EXTRINSICS  (per camera, two interchangeable paths; choose at capture time):
  A) checkerboard  (PRIMARY) — one frame per camera of a SHARED checkerboard that defines
     the world origin; cv2.solvePnP gives each camera's pose [R|t]. Use when all cameras
     can see the same board simultaneously.
  B) reference object / known 3D control points (ALTERNATIVE) — you supply N world points
     (X,Y,Z) on a known object; for each camera you pick the matching 2D image points
     (interactive picker or a precomputed JSON); cv2.solvePnP gives the pose. Use when no
     single board is visible to all cameras.

Writes <session>/<camera>/calibration.json  (intrinsics and/or extrinsics, with reproj error).

Usage:
  # intrinsics from a checkerboard video per camera (file: calib_intrinsics.mp4, else video.mp4)
  python calibrate.py intrinsics  --session sessions/<id> --cols 9 --rows 6 --square 0.025

  # extrinsics, shared checkerboard (primary): one detected frame per camera
  python calibrate.py extrinsics-board --session sessions/<id> --cols 9 --rows 6 --square 0.025

  # extrinsics, reference object: pick 2D points matching world points in points3d.json
  python calibrate.py extrinsics-object --session sessions/<id> --points3d points3d.json [--manual]

points3d.json: {"world_points": [[x,y,z], ...], "image_points": {"<cam>": [[u,v], ...]}}
  (image_points optional; with --manual you click them per camera instead.)

Requires: numpy, opencv-python (full build only needed for --manual GUI; headless is fine otherwise).
"""
import argparse
import json
from pathlib import Path

import numpy as np
import cv2


# ----------------------------------------------------------------------------
# geometry helpers (pure, unit-tested)
# ----------------------------------------------------------------------------
def board_object_points(cols, rows, square):
    """3D coordinates of inner checkerboard corners, board plane = z=0."""
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    return objp * float(square)


def reprojection_error(object_pts, image_pts, rvec, tvec, K, dist):
    proj, _ = cv2.projectPoints(object_pts, rvec, tvec, K, dist)
    proj = proj.reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum((proj - image_pts.reshape(-1, 2)) ** 2, axis=1))))


def calibrate_intrinsics_from_points(objpoints, imgpoints, image_size):
    """Core intrinsics solve (list of Nx3 object pts, list of Nx2 image pts)."""
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        objpoints, imgpoints, image_size, None, None)
    return K, dist, float(rms)


def solve_extrinsic(object_pts, image_pts, K, dist):
    """solvePnP -> (R 3x3, t 3x1, rvec, tvec, reproj_err)."""
    object_pts = np.asarray(object_pts, np.float32).reshape(-1, 3)
    image_pts = np.asarray(image_pts, np.float32).reshape(-1, 2)
    ok, rvec, tvec = cv2.solvePnP(object_pts, image_pts, K, dist,
                                  flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise RuntimeError("solvePnP failed")
    R, _ = cv2.Rodrigues(rvec)
    err = reprojection_error(object_pts, image_pts, rvec, tvec, K, dist)
    return R, tvec, rvec, tvec, err


# ----------------------------------------------------------------------------
# video / image utilities
# ----------------------------------------------------------------------------
def sample_gray_frames(video, n=25):
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    idxs = np.linspace(0, max(total - 1, 0), n).astype(int) if total else range(n)
    frames = []
    for i in idxs:
        if total:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
    cap.release()
    return frames


def grab_frame(video, t_sec=0.0):
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000)
    ok, f = cap.read()
    cap.release()
    return f if ok else None


def detect_board(gray, pattern):
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
    ok, corners = cv2.findChessboardCorners(gray, pattern, flags)
    if not ok:
        return None
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    return cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)


def manual_pick(frame, n, label):
    """Interactive 2D point picker (needs a display; opencv-python full build)."""
    pts = []
    disp = frame.copy()
    win = f"pick {n} points — {label} (click in world-point order, any key when done)"

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < n:
            pts.append([float(x), float(y)])
            cv2.circle(disp, (x, y), 5, (0, 255, 0), -1)
            cv2.putText(disp, str(len(pts)), (x + 6, y - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        cv2.imshow(win, disp)
        if cv2.waitKey(20) != -1 and len(pts) >= n:
            break
    cv2.destroyWindow(win)
    return pts


# ----------------------------------------------------------------------------
# calibration.json I/O
# ----------------------------------------------------------------------------
def load_calib(cam_dir):
    f = cam_dir / "calibration.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save_calib(cam_dir, data):
    (cam_dir / "calibration.json").write_text(json.dumps(data, indent=2))


def camera_dirs(session):
    return sorted(p for p in Path(session).iterdir()
                  if p.is_dir() and p.name != "sync"
                  and (list(p.glob("video.*")) or (p / "calibration.json").exists()))


# ----------------------------------------------------------------------------
# CLI commands
# ----------------------------------------------------------------------------
def cmd_intrinsics(args):
    pattern = (args.cols, args.rows)
    objp = board_object_points(args.cols, args.rows, args.square)
    for d in camera_dirs(args.session):
        vids = list(d.glob("calib_intrinsics.*")) or list(d.glob("video.*"))
        if not vids:
            print(f"  {d.name}: no video"); continue
        frames = sample_gray_frames(vids[0], args.frames)
        objpoints, imgpoints, size = [], [], None
        for g in frames:
            c = detect_board(g, pattern)
            if c is not None:
                objpoints.append(objp); imgpoints.append(c); size = g.shape[::-1]
        if len(objpoints) < 5:
            print(f"  {d.name}: only {len(objpoints)} usable board views (need >=5)"); continue
        K, dist, rms = calibrate_intrinsics_from_points(objpoints, imgpoints, size)
        data = load_calib(d)
        data["intrinsics"] = {"K": K.tolist(), "dist": dist.ravel().tolist(),
                              "image_size": list(size), "rms_px": rms, "views": len(objpoints)}
        save_calib(d, data)
        print(f"  {d.name}: intrinsics rms={rms:.3f}px from {len(objpoints)} views")


def cmd_extr_board(args):
    pattern = (args.cols, args.rows)
    objp = board_object_points(args.cols, args.rows, args.square)
    for d in camera_dirs(args.session):
        data = load_calib(d)
        if "intrinsics" not in data:
            print(f"  {d.name}: run intrinsics first"); continue
        K = np.array(data["intrinsics"]["K"]); dist = np.array(data["intrinsics"]["dist"])
        vids = list(d.glob("calib_extrinsics.*")) or list(d.glob("video.*"))
        frame = grab_frame(vids[0], args.frame_time) if vids else None
        if frame is None:
            print(f"  {d.name}: no extrinsics frame"); continue
        corners = detect_board(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), pattern)
        if corners is None:
            print(f"  {d.name}: shared board not detected"); continue
        R, t, rvec, tvec, err = solve_extrinsic(objp, corners, K, dist)
        data["extrinsics"] = {"method": "checkerboard", "R": R.tolist(),
                              "t": t.ravel().tolist(), "reproj_px": err}
        save_calib(d, data)
        print(f"  {d.name}: extrinsics (board) reproj={err:.3f}px")


def cmd_extr_object(args):
    spec = json.loads(Path(args.points3d).read_text())
    world = np.asarray(spec["world_points"], np.float32)
    provided = spec.get("image_points", {})
    for d in camera_dirs(args.session):
        data = load_calib(d)
        if "intrinsics" not in data:
            print(f"  {d.name}: run intrinsics first"); continue
        K = np.array(data["intrinsics"]["K"]); dist = np.array(data["intrinsics"]["dist"])
        if args.manual:
            vids = list(d.glob("calib_extrinsics.*")) or list(d.glob("video.*"))
            frame = grab_frame(vids[0], args.frame_time) if vids else None
            if frame is None:
                print(f"  {d.name}: no frame to pick on"); continue
            img_pts = manual_pick(frame, len(world), d.name)
        elif d.name in provided:
            img_pts = provided[d.name]
        else:
            print(f"  {d.name}: no image_points (use --manual or add to {args.points3d})"); continue
        R, t, rvec, tvec, err = solve_extrinsic(world, img_pts, K, dist)
        data["extrinsics"] = {"method": "reference_object", "R": R.tolist(),
                              "t": t.ravel().tolist(), "reproj_px": err,
                              "n_points": len(world)}
        save_calib(d, data)
        print(f"  {d.name}: extrinsics (object) reproj={err:.3f}px from {len(world)} points")


def main():
    ap = argparse.ArgumentParser(description="camera calibration (Step 7b)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("intrinsics")
    pi.add_argument("--session", required=True)
    pi.add_argument("--cols", type=int, default=9); pi.add_argument("--rows", type=int, default=6)
    pi.add_argument("--square", type=float, default=0.025, help="square size (m)")
    pi.add_argument("--frames", type=int, default=25)
    pi.set_defaults(func=cmd_intrinsics)

    pb = sub.add_parser("extrinsics-board")
    pb.add_argument("--session", required=True)
    pb.add_argument("--cols", type=int, default=9); pb.add_argument("--rows", type=int, default=6)
    pb.add_argument("--square", type=float, default=0.025)
    pb.add_argument("--frame-time", type=float, default=0.0, dest="frame_time")
    pb.set_defaults(func=cmd_extr_board)

    po = sub.add_parser("extrinsics-object")
    po.add_argument("--session", required=True)
    po.add_argument("--points3d", required=True)
    po.add_argument("--manual", action="store_true", help="pick 2D points interactively")
    po.add_argument("--frame-time", type=float, default=0.0, dest="frame_time")
    po.set_defaults(func=cmd_extr_object)

    args = ap.parse_args()
    print(f"calibrate: {args.cmd}")
    args.func(args)


if __name__ == "__main__":
    main()
