"""One-time script to generate PWA icons for Baseline.

Run once:  python generate_icons.py
Produces:  static/icons/icon-192.png, icon-512.png, apple-touch-icon.png
"""

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
    print("Done.")
