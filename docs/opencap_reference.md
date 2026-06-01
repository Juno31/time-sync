# OpenCap reference — what we borrow, what we change

OpenCap is the closest validated prior art (multi-iPhone markerless biomechanics, used for the same
downstream goal: 2D pose -> 3D reconstruction -> kinematics). This project deliberately mirrors its
proven design and is built to **feed OpenCap's own local pipeline**.

## What OpenCap is (sourced)
- Architecture: an **iOS app + web app + cloud** (Uhlrich et al., *PLOS Comput Biol* 2023, Q1; Apache-2.0; opencap.ai).
- Repos: **opencap-core** (2+ videos -> 3D markers + OpenSim kinematics), **opencap-processing** (dynamics/
  simulation), **opencap-iphone** (the iOS recorder app).
- Capture: **two or more iOS devices**; each records independently (native app, fixed settings ~60 fps),
  videos uploaded to the cloud.
- Calibration: a **printed checkerboard** is filmed to recover each camera's **intrinsics + extrinsics**
  (their code uses `cv2.solvePnP` / `SOLVEPNP_IPPE`). Required for triangulation.
- 2D pose: open-source pose estimators (OpenPose / HRNet) extract 2D keypoints from each view in the cloud.
- **Time sync (the crux): post-hoc cross-correlation of keypoint velocities in the image plane.**
  - *Gait trials:* cross-correlate the speeds of the **right vs left ankle** keypoints; if the max
    cross-correlation is large and the delay is **0.1–1 s**, it's classified as gait.
  - *Non-gait trials:* take the delay at the **max cross-correlation of the summed vertical speed of all
    keypoints** between cameras.
  - Preprocessing: confidence thresholding, occlusion handling, **Butterworth low-pass** filtering.
- 3D: **Direct Linear Transformation (DLT)** triangulation of the synchronized 2D keypoints; a deep network
  augments to anatomical markers; OpenSim scaling + inverse kinematics yield joint angles.
- **Local pipeline (their README, option 3):** opencap-core can run locally on "videos collected
  synchronously from another source." => our output should match what opencap-core expects.

## What we borrow
1. **2+ phones recording independently** (no hardware genlock) + post-hoc sync — the core OpenCap pattern.
2. **Checkerboard calibration** for intrinsics/extrinsics (new Step in our plan).
3. **Keypoint-velocity cross-correlation** as the sync refinement (Step 8 implements the exact method above).
4. **DLT triangulation + OpenSim/TRC output** as the downstream target — we produce compatible artifacts.

## Where we differ / improve
- **Fully local, no cloud.** Everything runs on the host PC over LAN (user requirement).
- **Web/PWA client, not a native app.** OpenCap's native app locks ~60 fps and reads hardware timestamps;
  our web client cannot, so our streams are VFR and we resample in post. (Accepted trade-off; see sync_design.md.)
- **Server-coordinated visual sync beacon** (synchronized flash + on-screen encoded ms timestamp). This is an
  **independent, motion-free hard reference** that OpenCap does not have — it protects us on low-motion or
  ambiguous trials where pure keypoint cross-correlation degrades. We use the beacon for coarse alignment,
  then cross-correlation for sub-frame refinement.
- **Scope boundary:** this project produces **synchronized + calibrated clips and a frame-offset map** in an
  opencap-core-compatible layout. We do **not** reimplement triangulation/biomechanics — the user runs
  opencap-core / opencap-processing (or Pose2Sim) on our output for the actual 3D reconstruction.

## Sources
- Uhlrich et al., OpenCap, *PLOS Computational Biology* 2023: https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1011462
- opencap-core: https://github.com/stanfordnmbl/opencap-core
- opencap-processing: https://github.com/stanfordnmbl/opencap-processing
- opencap-iphone: https://github.com/stanfordnmbl/opencap-iphone
- Pose2Sim (same sync family): https://github.com/perfanalytics/pose2sim
