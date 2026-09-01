# SPDX-License-Identifier: CC0-1.0

"""use_webcam — capture a photo from the host machine's webcam and
attach it to the conversation.

Returns a JSON string `{"ok": true, "attachment": "<rel_path>",
"message": "..."}` on success; the tool-loop layer in llm_backend
detects this shape and follows up by injecting a synthetic user turn
with the image attached, so the model can actually SEE what it just
captured (otherwise it'd only see this JSON text and have nothing to
describe).

opencv-python is an optional dependency — same model as trafilatura.
Try-import inside `capture_to_bytes`; if missing, returns a friendly
error pointing the user at `pip install opencv-python` rather than
crashing.

For privacy: callers (the Telegram path specifically) wrap this
executor in an approval flow so the operator can deny / approve /
auto-approve per user. This file knows nothing about that — the
wrapping happens at frame time, same as exec.
"""

import json


_WARMUP_FRAMES = 5
# JPEG quality 90 — good visual fidelity at ~50-200 KB for typical
# 720p webcams. Going higher just inflates the file without
# meaningful gain at this resolution; lower starts showing blockiness
# on text / fine detail. 90 is the conventional sweet spot.
_JPEG_QUALITY = 90


def capture_to_bytes(camera_index=0):
    """Capture one frame from the host webcam and return JPEG bytes.

    Raises RuntimeError with a human-readable message if opencv-python
    isn't installed, the camera can't be opened, or the read fails.

    Warmup: opencv's first .read() after VideoCapture() open often
    returns a black or autofocus-pending frame because the sensor is
    still adjusting exposure / white balance / focus. We discard a few
    early frames before grabbing the one we keep — standard practice.
    """
    try:
        import cv2
    except ImportError:
        raise RuntimeError(
            "Webcam capture needs opencv-python. Install with "
            "`pip install opencv-python` (or the bundled build "
            "needs to include it)."
        )

    cap = cv2.VideoCapture(camera_index)
    try:
        if not cap.isOpened():
            raise RuntimeError(
                f"Couldn't open camera (index {camera_index}). "
                f"Is another app using it, or is there no webcam?"
            )
        # Warm up: discard a few frames so the captured one isn't
        # the autofocus-pending black square.
        for _ in range(_WARMUP_FRAMES):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(
                "Camera opened but the read failed. Try again, or "
                "check whether another app has grabbed the camera "
                "mid-capture."
            )
        encoded_ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY]
        )
        if not encoded_ok:
            raise RuntimeError("JPEG encoding failed.")
        return buf.tobytes()
    finally:
        cap.release()


def use_webcam(agent_name: str = "") -> str:
    """Take a photo using the host machine's webcam and attach it to
    the conversation so you can see what's in front of the camera.

    Pass no arguments — there's nothing for the model to choose. The
    photo is captured from the system's default webcam (camera index
    0), saved into this kin's attachments directory, and the next
    turn in this conversation will include the image as a visible
    attachment.

    Returns a JSON string describing what happened. On success, the
    framework follows up by feeding the captured image back as a
    user turn — you'll see it directly on the next inference.
    """
    from kin_persistence import save_attachment_bytes

    if not agent_name:
        return json.dumps({
            "ok": False,
            "error": "use_webcam needs an agent context.",
        })

    try:
        data = capture_to_bytes(camera_index=0)
    except RuntimeError as e:
        return json.dumps({"ok": False, "error": str(e)})
    except Exception as e:
        return json.dumps({
            "ok": False,
            "error": f"Webcam capture failed: {e}",
        })

    try:
        rel = save_attachment_bytes(agent_name, data, mime_type="image/jpeg")
    except Exception as e:
        return json.dumps({
            "ok": False,
            "error": f"Couldn't save captured photo: {e}",
        })

    return json.dumps({
        "ok": True,
        "attachment": rel,
        "message": "Webcam photo captured. The image will be visible "
                   "on your next turn — describe what you see, or "
                   "respond as the user asked.",
    })
