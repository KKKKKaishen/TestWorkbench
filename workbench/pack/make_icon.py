"""从照片生成应用图标：pack/cyu.ico（exe/安装器）+ web/public/favicon.ico。"""
import os

from PIL import Image

PACK = os.path.dirname(os.path.abspath(__file__))
src = Image.open(os.path.join(PACK, "icon_source.jpg")).convert("RGBA")
w, h = src.size
side = min(w, h)
top = max(0, int((h - side) * 0.25))  # 偏上取人物主体
sq = src.crop(((w - side) // 2, top, (w - side) // 2 + side, top + side))
sq = sq.resize((256, 256), Image.LANCZOS)  # 统一到 256 底图，缩小不失真
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
ico_path = os.path.join(PACK, "cyu.ico")
sq.save(ico_path, sizes=sizes)
os.makedirs(os.path.join(PACK, "..", "web", "public"), exist_ok=True)
sq.save(os.path.join(PACK, "..", "web", "public", "favicon.ico"), sizes=sizes)
print("ICO_OK", os.path.getsize(ico_path))
