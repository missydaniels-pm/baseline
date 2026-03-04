"""One-time script to generate PWA icons for Baseline.

Run once:  python generate_icons.py
Produces:  static/icons/icon-192.png, icon-512.png, apple-touch-icon.png
"""

from PIL import Image, ImageDraw

BG = (124, 58, 237)   # #7c3aed — vibrant purple
LINE = (255, 255, 255) # white


def draw_baseline_icon(size):
    """Draw a bold white EKG pulse on a purple background."""
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # Baseline sits slightly below center for visual balance
    cy = int(size * 0.55)
    margin = int(size * 0.15)
    line_w = max(4, round(size * 0.05))

    # EKG-style points: flat — small dip — sharp spike — small dip — flat
    points = [
        (margin,              cy),                  # left start
        (int(size * 0.36),    cy),                  # approach pulse
        (int(size * 0.40),    cy + int(size*0.06)), # small dip down
        (int(size * 0.46),    cy - int(size*0.30)), # sharp spike up
        (int(size * 0.52),    cy + int(size*0.10)), # overshoot down
        (int(size * 0.58),    cy),                  # return to baseline
        (size - margin,       cy),                  # right end
    ]

    draw.line(points, fill=LINE, width=line_w, joint="curve")

    # Round the line endpoints with small circles
    r = line_w // 2
    for pt in (points[0], points[-1]):
        draw.ellipse([pt[0]-r, pt[1]-r, pt[0]+r, pt[1]+r], fill=LINE)

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
