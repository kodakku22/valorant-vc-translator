"""Generate assets/icon.ico (subtitle/speech-bubble motif). Build-time only, needs Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"
OUT.parent.mkdir(exist_ok=True)

img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# dark app tile
d.rounded_rectangle([8, 8, 248, 248], radius=48, fill=(18, 18, 24, 255))
# speech bubble
d.rounded_rectangle([40, 56, 216, 152], radius=26, fill=(242, 242, 246, 255))
d.polygon([(84, 148), (120, 148), (72, 190)], fill=(242, 242, 246, 255))
# lines inside the bubble: red (VALORANT accent) + dark
d.rounded_rectangle([62, 82, 194, 96], radius=7, fill=(255, 70, 85, 255))
d.rounded_rectangle([62, 112, 160, 126], radius=7, fill=(60, 60, 70, 255))
# subtitle bars below (EN grey, JA white)
d.rounded_rectangle([48, 186, 208, 200], radius=7, fill=(130, 138, 148, 255))
d.rounded_rectangle([48, 212, 176, 230], radius=8, fill=(255, 255, 255, 255))

img.save(OUT, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
print(f"icon written: {OUT}")
