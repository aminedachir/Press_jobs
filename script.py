import cv2
import numpy as np
import mediapipe as mp

# ----------------------------
# Foreground and background videos
# ----------------------------
fg_cap = cv2.VideoCapture("video.webm")       # Your original video
bg_cap = cv2.VideoCapture("studio_fixed.mp4") # Studio video background

if not fg_cap.isOpened():
    raise FileNotFoundError("video.webm not found or cannot be opened")
if not bg_cap.isOpened():
    raise FileNotFoundError("studio_video.mp4 not found or cannot be opened")

# Get foreground video properties
width  = int(fg_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(fg_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps    = fg_cap.get(cv2.CAP_PROP_FPS)

# ----------------------------
# Prepare output video writer
# ----------------------------
fourcc = cv2.VideoWriter_fourcc(*'avc1')  # H.264
out = cv2.VideoWriter("output_video.mp4", fourcc, fps, (width, height))

# ----------------------------
# Initialize MediaPipe Selfie Segmentation
# ----------------------------
mp_selfie = mp.solutions.selfie_segmentation
segment = mp_selfie.SelfieSegmentation(model_selection=1)

# ----------------------------
# Process frames
# ----------------------------
while fg_cap.isOpened():
    ret_fg, fg_frame = fg_cap.read()
    ret_bg, bg_frame = bg_cap.read()

    if not ret_fg:
        break

    # If background video ends, loop it
    if not ret_bg:
        bg_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret_bg, bg_frame = bg_cap.read()

    # Resize background frame
    bg_frame = cv2.resize(bg_frame, (width, height))

    # Convert foreground to RGB
    rgb_frame = cv2.cvtColor(fg_frame, cv2.COLOR_BGR2RGB)

    # Run segmentation
    results = segment.process(rgb_frame)

    # Soft mask
    mask = results.segmentation_mask
    mask_3ch = np.stack((mask,) * 3, axis=-1)
    mask_3ch = cv2.GaussianBlur(mask_3ch, (15, 15), 0)

    # Blend foreground and background
    output_frame = fg_frame * mask_3ch + bg_frame * (1 - mask_3ch)
    output_frame = output_frame.astype(np.uint8)

    # Write output frame
    out.write(output_frame)

    # Show preview
    cv2.imshow("Background Replacement Preview", output_frame)
    if cv2.waitKey(1) & 0xFF == 27:  # ESC to exit
        break

# Release resources
fg_cap.release()
bg_cap.release()
out.release()
cv2.destroyAllWindows()