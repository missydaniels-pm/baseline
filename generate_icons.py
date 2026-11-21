"""One-time script to generate PWA icons for Baseline.

Run once:  .venv/bin/pip install Pillow && .venv/bin/python generate_icons.py
           (Pillow is dev-only, deliberately not in requirements.in.)
Produces:  static/icons/icon-192.png, icon-512.png, apple-touch-icon.png
           mobile/assets/icon.png (1024px, the iPhone "Baseline Sync" app: the same
           pulse plus a sync badge, so it can't be mistaken for the web app's
           Home Screen icon — owner decision 11/21/26)
"""

import math

from PIL import Image, ImageDraw

BG = (124, 58, 237)    # #7c3aed — vibrant purple
LINE = (255, 255, 255)  # white


def draw_baseline_icon(size):
    """Draw a bold white EKG pulse on a solid purple background.

    The entire canvas is purple (no transparency, no border, no rounded rect)
    so iOS displays the purple directly rather than compositing onto black.
    """
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # Bold stroke: ~8-10px at 512, scales proportionally
    line_w = max(6, round(size * 0.019) * 2)  # keep even for clean rendering

    # Pulse occupies ~60% of canvas width, centered
    left = int(size * 0.20)
    right = int(size * 0.80)
    cy = int(size * 0.54)  # slightly below center for visual balance

    # EKG points: flat — dip — sharp spike — overshoot — flat
    points = [
        (left,               cy),                   # left end of baseline
        (int(size * 0.38),   cy),                   # approach
        (int(size * 0.42),   cy + int(size * 0.06)),# small dip
        (int(size * 0.48),   cy - int(size * 0.28)),# spike up
        (int(size * 0.54),   cy + int(size * 0.09)),# overshoot down
        (int(size * 0.60),   cy),                   # return to baseline
        (right,              cy),                   # right end of baseline
    ]

    draw.line(points, fill=LINE, width=line_w, joint="curve")

    # Round the two endpoints
    r = line_w // 2
    for pt in (points[0], points[-1]):
        draw.ellipse([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r], fill=LINE)

    return img


def draw_sync_icon(size):
    """The Baseline icon with a white sync badge (two circular arrows) in the
    lower right, kept inside iOS's rounded-corner mask."""
    img = draw_baseline_icon(size)
    draw = ImageDraw.Draw(img)
    cx, cy, r = size * 0.75, size * 0.76, size * 0.15
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=LINE)

    ar = r * 0.55                 # arrow ring radius
    w = max(4, int(size * 0.032))  # arrow stroke
    box = [cx - ar, cy - ar, cx + ar, cy + ar]
    for start in (200, 20):        # two arcs, each with a head at its clockwise end
        end = start + 115
        draw.arc(box, start, end, fill=BG, width=w)
        a = math.radians(end)
        rc = ar - w / 2               # PIL draws a thick arc inward from its box
        px, py = cx + rc * math.cos(a), cy + rc * math.sin(a)
        tx, ty = -math.sin(a), math.cos(a)          # clockwise tangent
        nx, ny = math.cos(a), math.sin(a)           # outward normal
        length, half = w * 1.5, w * 1.15
        tip = (px + tx * length, py + ty * length)
        b1 = (px + nx * half, py + ny * half)
        b2 = (px - nx * half, py - ny * half)
        draw.polygon([tip, b1, b2], fill=BG)
    return img


if __name__ == "__main__":
    sizes = {
        "static/icons/icon-512.png": 512,
        "static/icons/icon-192.png": 192,
        "static/icons/apple-touch-icon.png": 180,
    }
    for path, sz in sizes.items():
        img = draw_baseline_icon(sz)
        img.save(path, "PNG")
        print(f"  {path}  ({sz}x{sz})")
    draw_sync_icon(1024).save("mobile/assets/icon.png", "PNG")
    print("  mobile/assets/icon.png  (1024x1024, sync badge)")
    print("Done.")
