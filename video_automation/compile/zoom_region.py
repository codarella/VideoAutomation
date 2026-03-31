"""
Zoom-to-region effect — smoothly zooms from full image into a region of interest.

Used when the narrator is explaining a specific phenomenon visible in an image.
The viewer sees the full picture first, then the camera zooms into the relevant area.

Usage (standalone test):
    python -m video_automation.compile.zoom_region <image_path> [--region x,y,w,h] [--duration 6]

Region coordinates are normalized 0-1:
    x, y = top-left corner of the region
    w, h = width and height of the region
    Example: 0.3,0.2,0.4,0.4 = center-ish box covering 40% of the image
"""

from __future__ import annotations

import math
import os
import subprocess
from dataclasses import dataclass


@dataclass
class ZoomRegion:
    """A normalized region of interest within an image (all values 0-1)."""
    x: float      # left edge
    y: float      # top edge
    w: float      # width
    h: float      # height

    @property
    def cx(self) -> float:
        """Center X."""
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        """Center Y."""
        return self.y + self.h / 2

    @property
    def zoom_factor(self) -> float:
        """How much to zoom in — inverse of the region's largest dimension."""
        # If region is 40% of image width, zoom factor = 1/0.4 = 2.5x
        # Cap at 3x to avoid pixelation
        return min(1 / max(self.w, self.h), 3.0)

    @classmethod
    def from_dict(cls, d: dict) -> ZoomRegion:
        return cls(x=d["x"], y=d["y"], w=d["w"], h=d["h"])

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def build_zoom_region_filter(
    region: ZoomRegion,
    duration: float,
    fps: int = 30,
    hold_start: float = 0.5,
    hold_end: float = 1.0,
    out_w: int = 1920,
    out_h: int = 1080,
) -> str:
    """
    Build an ffmpeg zoompan filter that zooms from full image into a region.

    Timeline:
        [0 .. hold_start]     — hold full image (zoom=1)
        [hold_start .. end-hold_end] — smooth zoom into region
        [end-hold_end .. end] — hold on zoomed region

    Args:
        region: Target region of interest (normalized coords)
        duration: Total clip duration in seconds
        fps: Frame rate
        hold_start: Seconds to hold the full image before zooming
        hold_end: Seconds to hold the zoomed-in view at the end
        out_w, out_h: Output resolution
    """
    total_frames = max(int(math.ceil(duration * fps)) + 1, 1)
    hold_start_frames = int(hold_start * fps)
    hold_end_frames = int(hold_end * fps)
    zoom_frames = max(total_frames - hold_start_frames - hold_end_frames, 1)

    target_zoom = region.zoom_factor

    # Center of the region in zoompan coordinates
    # In zoompan: x = pixel offset of the top-left corner of the visible area
    # At zoom Z, visible area = (iw/Z, ih/Z)
    # To center on (cx, cy): x = cx*iw - iw/(2*Z), y = cy*ih - ih/(2*Z)
    cx = region.cx
    cy = region.cy

    # Frame ranges
    f_start = hold_start_frames
    f_end = f_start + zoom_frames

    # Zoom expression: ramp from 1.0 to target_zoom using smooth easing
    # progress = clamp((frame - f_start) / zoom_frames, 0, 1)
    # zoom = 1 + progress * (target_zoom - 1)   [with smooth easing via sin]
    z_delta = target_zoom - 1.0

    # Using ffmpeg expressions:
    # on = current frame number (starts at 0)
    # Smooth ease-in-out: sin(progress * PI/2)^2 gives nice acceleration/deceleration
    zoom_expr = (
        f"if(lt(on,{f_start}),1,"                          # hold start: zoom=1
        f"if(gt(on,{f_end}),{target_zoom:.4f},"            # hold end: zoom=target
        f"1+{z_delta:.4f}*"                                 # ramp: 1 + delta * eased_progress
        f"((on-{f_start})/{zoom_frames})"                   # linear progress (simple for now)
        f"))"
    )

    # X expression: smoothly move from center of image to center of region
    # At zoom=1: x = iw/2 - iw/2 = 0 (or we want to start centered)
    # At zoom=Z targeting (cx,cy): x = cx*iw - iw/(2*Z)
    # Interpolate between start_x and end_x using same progress
    x_expr = (
        f"if(lt(on,{f_start}),iw/2-(iw/zoom/2),"
        f"if(gt(on,{f_end}),{cx:.4f}*iw-(iw/zoom/2),"
        f"(iw/2-(iw/zoom/2))"                               # start position
        f"+((on-{f_start})/{zoom_frames})"                   # progress
        f"*({cx:.4f}*iw-(iw/zoom/2)-(iw/2-(iw/zoom/2)))"   # delta to target
        f"))"
    )

    y_expr = (
        f"if(lt(on,{f_start}),ih/2-(ih/zoom/2),"
        f"if(gt(on,{f_end}),{cy:.4f}*ih-(ih/zoom/2),"
        f"(ih/2-(ih/zoom/2))"
        f"+((on-{f_start})/{zoom_frames})"
        f"*({cy:.4f}*ih-(ih/zoom/2)-(ih/2-(ih/zoom/2)))"
        f"))"
    )

    # Full filter: upscale for quality, then zoompan, then output scale
    vf = (
        f"scale=3840:2160:force_original_aspect_ratio=increase,"
        f"crop=3840:2160,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
        f":d={total_frames}:s={out_w}x{out_h}:fps={fps},"
        f"setsar=1"
    )
    return vf


def encode_zoom_region_clip(
    image_path: str,
    output_path: str,
    region: ZoomRegion,
    duration: float = 6.0,
    fps: int = 30,
    hold_start: float = 0.5,
    hold_end: float = 1.0,
) -> bool:
    """Encode an image into a video clip with the zoom-to-region effect."""
    vf = build_zoom_region_filter(
        region=region,
        duration=duration,
        fps=fps,
        hold_start=hold_start,
        hold_end=hold_end,
    )

    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-loop", "1", "-framerate", str(fps),
        "-i", image_path,
        "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-vf", vf,
        output_path,
    ]

    kwargs = dict(capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    print(f"   Encoding zoom-to-region clip: {os.path.basename(image_path)}")
    print(f"   Region: ({region.x:.2f}, {region.y:.2f}) {region.w:.2f}x{region.h:.2f}")
    print(f"   Zoom factor: {region.zoom_factor:.1f}x, Duration: {duration:.1f}s")

    r = subprocess.run(cmd, **kwargs)
    if r.returncode != 0:
        print(f"   ERROR: {r.stderr[-500:]}")
        return False

    print(f"   OK: {output_path}")
    return True


# ── Standalone test ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test zoom-to-region effect on an image")
    parser.add_argument("image", help="Path to input image")
    parser.add_argument("--region", default="0.3,0.2,0.4,0.4",
                        help="Region as x,y,w,h (normalized 0-1). Default: 0.3,0.2,0.4,0.4")
    parser.add_argument("--duration", type=float, default=6.0, help="Clip duration in seconds")
    parser.add_argument("--hold-start", type=float, default=0.5, help="Seconds to hold full view")
    parser.add_argument("--hold-end", type=float, default=1.0, help="Seconds to hold zoomed view")
    parser.add_argument("-o", "--output", default=None, help="Output path (default: <image>_zoom.mp4)")
    args = parser.parse_args()

    x, y, w, h = [float(v) for v in args.region.split(",")]
    region = ZoomRegion(x=x, y=y, w=w, h=h)

    output = args.output or os.path.splitext(args.image)[0] + "_zoom.mp4"

    success = encode_zoom_region_clip(
        image_path=args.image,
        output_path=output,
        region=region,
        duration=args.duration,
        hold_start=args.hold_start,
        hold_end=args.hold_end,
    )

    if success:
        print(f"\n   Done! Open {output} to see the effect.")
    else:
        print("\n   Failed to encode clip.")
        exit(1)
