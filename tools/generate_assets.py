from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)


def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype("segoeuib.ttf" if bold else "segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default()


def progress(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], used: int) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=3, fill="#303030")
    color = "#ED646A" if used >= 85 else "#E8A13B" if used >= 65 else "#5B8DEF"
    draw.rounded_rectangle((x1, y1, x1 + round((x2 - x1) * used / 100), y2), radius=3, fill=color)


icon = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(icon)
draw.rounded_rectangle((8, 8, 248, 248), radius=62, fill="#111318", outline="#55C985", width=14)
draw.text((128, 124), "42", anchor="mm", font=font(100, True), fill="#F5F7FA")
icon.save(ASSETS / "limitbar.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

preview = Image.new("RGB", (1100, 650), "#0C0D10")
d = ImageDraw.Draw(preview)
d.text((54, 44), "LimitBar 0.2", font=font(34, True), fill="#F3F3F3")
d.text((54, 86), "Taskbar glance + animated desktop detail", font=font(18), fill="#969696")

# Desktop widget
card = (54, 135, 610, 550)
d.rounded_rectangle(card, radius=22, fill="#1E1E1E", outline="#3B3B3B", width=2)
d.text((78, 165), "Plan usage limits", font=font(20, True), fill="#F1F1F1")
d.text((78, 195), "Claude + ChatGPT / Codex", font=font(13), fill="#969696")
d.text((566, 172), "↻   ×", anchor="rm", font=font(17), fill="#969696")
d.line((74, 221, 590, 221), fill="#343434", width=2)

y = 252
sections = [
    ("#E6A057", "Claude", [("5-hour limit", "Reset 2h 18m", 27), ("Weekly · all models", "Reset 4d 7h", 41)]),
    ("#79A7FF", "ChatGPT / Codex", [("5-hour limit", "Reset 3h 2m", 36), ("Weekly · all models", "Reset 5d 11h", 18)]),
]
for section_index, (accent, name, rows) in enumerate(sections):
    d.ellipse((78, y - 5, 88, y + 5), fill=accent)
    d.text((98, y), name, anchor="lm", font=font(14, True), fill="#F1F1F1")
    y += 28
    for label, reset, used in rows:
        d.text((78, y), label, anchor="lm", font=font(13, True), fill="#F1F1F1")
        d.text((585, y), f"{reset}   {used}% used", anchor="rm", font=font(12), fill="#969696")
        progress(d, (78, y + 15, 585, y + 22), used)
        y += 49
    if section_index == 0:
        d.line((74, y - 3, 590, y - 3), fill="#343434", width=2)
        y += 18

# Taskbar strip concept
d.text((676, 156), "Inside the taskbar", font=font(16, True), fill="#F1F1F1")
d.text((676, 184), "Click to show or hide the detail widget", font=font(12), fill="#969696")
d.rounded_rectangle((676, 230, 1046, 280), radius=10, fill="#202020", outline="#383838")
d.line((861, 238, 861, 272), fill="#3A3A3A", width=2)
for x, accent, label, remaining, used in [(690, "#E6A057", "Claude", 59, 41), (875, "#79A7FF", "ChatGPT", 82, 18)]:
    d.ellipse((x, 242, x + 8, 250), fill=accent)
    d.text((x + 16, 246), label, anchor="lm", font=font(11, True), fill="#F1F1F1")
    d.text((x + 161, 246), f"{remaining}% left", anchor="rm", font=font(10, True), fill="#55C985")
    progress(d, (x, 263, x + 161, 268), used)

d.rounded_rectangle((676, 330, 1046, 474), radius=16, fill="#15171B")
d.text((700, 356), "COLOR STATES", font=font(10, True), fill="#969696")
states = [("Normal", "#5B8DEF", "0–64% used"), ("Warning", "#E8A13B", "65–84% used"), ("Critical", "#ED646A", "85–100% used")]
for index, (name, color, description) in enumerate(states):
    yy = 390 + index * 30
    d.rounded_rectangle((700, yy - 6, 712, yy + 6), radius=3, fill=color)
    d.text((726, yy), name, anchor="lm", font=font(12, True), fill="#F1F1F1")
    d.text((1018, yy), description, anchor="rm", font=font(11), fill="#969696")

preview.save(ASSETS / "preview.png")
