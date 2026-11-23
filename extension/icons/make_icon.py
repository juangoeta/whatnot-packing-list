# python3 make_icon.py <outdir>  (regenerates icon16/32/48/128.png)
# Who Got What icon: rounded square, pink-to-plum gradient, a white W drawn as two check marks.
import sys
from PIL import Image, ImageDraw, ImageFilter
S = 1024
out = sys.argv[1]

def hexc(h): return tuple(int(h[i:i+2], 16) for i in (1, 3, 5))
c0, c1 = hexc("#F07AAE"), hexc("#9E2F60")      # top-left -> bottom-right
grad = Image.new("RGB", (S, S))
px = grad.load()
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * (S - 1))
        px[x, y] = tuple(round(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.235), fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(grad, (0, 0), mask)

# soft highlight in the top-left corner
hl = Image.new("L", (S, S), 0)
ImageDraw.Draw(hl).ellipse((-S * 0.3, -S * 0.55, S * 0.75, S * 0.45), fill=70)
hl = hl.filter(ImageFilter.GaussianBlur(S * 0.12))
white = Image.new("RGBA", (S, S), (255, 255, 255, 255))
icon.paste(white, (0, 0), Image.composite(hl, Image.new("L", (S, S), 0), mask))

# the W: two check marks, outer tops higher than the middle peak
d = ImageDraw.Draw(icon)
pts = [(208, 318), (372, 706), (512, 470), (652, 706), (816, 318)]
w = 118
# shadow
sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(sh)
off = 14
sd.line([(x, y + off) for x, y in pts], fill=(70, 10, 40, 110), width=w, joint="curve")
for x, y in pts: sd.ellipse((x - w / 2, y + off - w / 2, x + w / 2, y + off + w / 2), fill=(70, 10, 40, 110))
sh = sh.filter(ImageFilter.GaussianBlur(10))
icon = Image.alpha_composite(icon, Image.composite(sh, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))
d = ImageDraw.Draw(icon)
d.line(pts, fill=(255, 255, 255, 255), width=w, joint="curve")
for x, y in pts: d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=(255, 255, 255, 255))

for n in (16, 32, 48, 128, 512):
    icon.resize((n, n), Image.LANCZOS).save(f"{out}/icon{n}.png", optimize=True)
print("ok")
