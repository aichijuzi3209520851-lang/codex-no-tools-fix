#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 Codex 工具修复 —— 图标生成器
===============================================================================

设计概念：「提示符已唤醒」(The Prompt Is Back)

  一枚 squircle（超椭圆）底片，深空蓝到近黑的斜向渐变，左上一团青、
  右下一团紫的柔光。正中是一枚加粗的命令行提示符 `❯`，右侧接一条
  青→紫渐变的块状光标。

  为什么是这个图形：
    这个脚本修的是"模型拿不到终端工具"——修好之后提示符重新可用。
    `❯` + 光标块是终端最本质的两个符号，完全避开了齿轮、扳手、螺丝刀
    这类被用滥的"修复/设置"套话图形。

  为什么小尺寸还认得出：
    全图只有两团高对比实心形状，没有细线、没有文字、不靠颜色传义。
    16/20/24/32 用一套更粗的参数单独重绘，不是把 1024 直接缩小。

  三条硬指标（由 _check_icon.py 校验）：
    1. 光学居中：按"着色质量质心"对齐画布中心，不是按外框对齐
    2. 各尺寸视觉体量一致：不透明面积占画布的比例在各档之间偏差 < 6%
    3. 小尺寸不糊：V 形与光标块之间必须仍留有背景间隙

输出：
  assets/codex-tool-fix.ico   多尺寸图标（16/20/24/32/40/48/64/96/128/256）
  assets/codex-tool-fix.png   1024 预览（仓库首页用）

复现：python _make_icon.py
===============================================================================
"""

import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BASE = 1024                 # 设计基准边长
SS = 2                      # 超采样倍率
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
PNG_PREVIEW = 1024

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")

# ----------------------------------------------------------------------------
# 设计令牌
# ----------------------------------------------------------------------------
TILE_N = 4.6                # squircle 指数（4~5 之间最像现代 App 图标）

# 底片渐变：深空蓝 → 近黑
BG_A = (0x18, 0x1F, 0x33)
BG_B = (0x08, 0x0A, 0x12)
# 两团环境光
GLOW_CYAN = (0x21, 0xCF, 0xEB)
GLOW_VIOLET = (0x7C, 0x86, 0xF5)
# 提示符配色
CHEV_HI = (0xFF, 0xFF, 0xFF)
CHEV_LO = (0x9A, 0xCF, 0xFF)
BAR_HI = (0x2B, 0xD4, 0xEE)
BAR_LO = (0x8A, 0x8E, 0xFF)

# 两套参数：大尺寸留细节，小尺寸只求笔画粗、对比强、不糊。
PROFILES = {
    "large": dict(
        margin=40, stroke=92, chev_w=196, gap=34,
        bar_w=150, bar_h=92,
        chev_top=330, chev_bottom=706,
        glow=True, orbit=True, rim=0.16, sheen=True,
    ),
    "small": dict(
        # gap 必须 >= 64 个基准单位：16px 下 1 像素 = 64 基准单位，
        # 否则 V 形与光标块会糊成一块（间隙 0px），小尺寸就失去辨识度。
        margin=30, stroke=118, chev_w=188, gap=86,
        bar_w=138, bar_h=116,
        chev_top=316, chev_bottom=712,
        glow=False, orbit=False, rim=0.12, sheen=False,
    ),
}


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------

def superellipse_mask(size, inset, n=TILE_N):
    """超椭圆（squircle）实心遮罩，0..1 float32。"""
    c = size / 2.0
    a = (size - 2.0 * inset) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d = (np.abs(xx + 0.5 - c) / a) ** n + (np.abs(yy + 0.5 - c) / a) ** n
    return np.clip((1.0 - d) * (a * 0.5), 0.0, 1.0)


def linear_rgb(size, c0, c1, p0, p1):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    denom = dx * dx + dy * dy
    t = ((xx - p0[0]) * dx + (yy - p0[1]) * dy) / (denom if denom else 1e-6)
    t = np.clip(t, 0.0, 1.0)[..., None]
    a, b = np.array(c0, np.float32), np.array(c1, np.float32)
    return a * (1.0 - t) + b * t


def radial_rgb(size, cx, cy, radius, color, strength, falloff=2.0):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(radius, 1.0)
    a = np.clip(1.0 - d, 0.0, 1.0) ** falloff * strength
    return a[..., None] * np.array(color, np.float32)


def shift_mask(arr, dx, dy):
    """亚像素平移一张 0..1 遮罩。"""
    img = Image.fromarray(arr.astype(np.float32), mode="F")
    out = img.transform(img.size, Image.AFFINE, (1, 0, -dx, 0, 1, -dy),
                        resample=Image.BILINEAR)
    return np.asarray(out, dtype=np.float32)


def mass_centroid(arr):
    total = float(arr.sum())
    if total <= 0:
        return arr.shape[1] / 2.0, arr.shape[0] / 2.0
    yy, xx = np.mgrid[0:arr.shape[0], 0:arr.shape[1]].astype(np.float32)
    return float((arr * xx).sum() / total), float((arr * yy).sum() / total)


# ----------------------------------------------------------------------------
# 字形几何
# ----------------------------------------------------------------------------

def glyph_masks(p, dx=0.0, dy=0.0):
    """生成 V 形与光标块两张遮罩，并叠加给定量心的亚像素位移。"""
    S = BASE * SS

    def P(v):
        return v * SS

    stroke = p["stroke"]
    painted_chev = p["chev_w"] + stroke          # 含圆头外溢
    total = painted_chev + p["gap"] + p["bar_w"]
    left = BASE / 2.0 - total / 2.0

    left_x = left + stroke / 2.0
    apex_x = left_x + p["chev_w"]
    mid_y = (p["chev_top"] + p["chev_bottom"]) / 2.0
    pts = [(P(left_x + dx), P(p["chev_top"] + dy)),
           (P(apex_x + dx), P(mid_y + dy)),
           (P(left_x + dx), P(p["chev_bottom"] + dy))]

    chev_img = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(chev_img)
    d.line(pts, fill=255, width=P(stroke), joint="curve")
    r = P(stroke) / 2.0
    for cx, cy in pts:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    chev = np.asarray(chev_img, dtype=np.float32) / 255.0

    bl = left + painted_chev + p["gap"] + dx
    bt = p["chev_bottom"] - p["bar_h"] + dy
    br = bl + p["bar_w"]
    bb = bt + p["bar_h"]
    bar_img = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bar_img).rounded_rectangle(
        (P(bl), P(bt), P(br), P(bb)),
        radius=P(min(p["bar_h"], p["bar_w"]) / 2.0), fill=255)
    bar = np.asarray(bar_img, dtype=np.float32) / 255.0

    return chev, bar


def centered_glyphs(p):
    """迭代一次：按"质量质心"把整个字形摆到画布正中。"""
    chev, bar = glyph_masks(p)
    g = np.maximum(chev, bar)
    cx, cy = mass_centroid(g)
    mid = BASE * SS / 2.0
    dx, dy = (mid - cx) / SS, (mid - cy) / SS
    chev2, bar2 = glyph_masks(p, dx, dy)
    g2 = np.maximum(chev2, bar2)
    cx2, cy2 = mass_centroid(g2)
    return chev2, bar2, ((cx2 - mid) / SS, (cy2 - mid) / SS)


# ----------------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------------

def render(profile_name):
    p = PROFILES[profile_name]
    S = BASE * SS

    def P(v):
        return v * SS

    tile = superellipse_mask(S, P(p["margin"]))
    tile_box_mask = tile

    # ---- 底片：斜向渐变 + 两团环境光 ----
    rgb = linear_rgb(S, BG_A, BG_B, (0, 0), (S, S))
    rgb += radial_rgb(S, P(280), P(230), P(760), GLOW_CYAN, 0.32)
    rgb += radial_rgb(S, P(790), P(820), P(840), GLOW_VIOLET, 0.30)

    # 顶部一层玻璃反光，让方块不显得死平
    if p["sheen"]:
        yy = np.mgrid[0:S, 0:S][0].astype(np.float32) / S
        sheen = np.clip(1.0 - yy / 0.46, 0.0, 1.0) ** 2.2 * 0.085
        rgb += (sheen * 255.0)[..., None]

    rgb = np.clip(rgb, 0.0, 255.0)

    # ---- 字形 ----
    chev, bar, resid = centered_glyphs(p)
    gmask = np.maximum(chev, bar)

    chev_col = linear_rgb(S, CHEV_HI, CHEV_LO,
                          (0, P(p["chev_top"])), (0, P(p["chev_bottom"])))
    painted_chev = p["chev_w"] + p["stroke"]
    bar_l = BASE / 2.0 + (-(painted_chev + p["gap"] + p["bar_w"])) / 2.0 \
        + painted_chev + p["gap"]
    bar_col = linear_rgb(S, BAR_HI, BAR_LO,
                         (P(bar_l), 0), (P(bar_l + p["bar_w"]), 0))

    # ---- 轨道细环（仅大尺寸，几乎不可见，只加一点结构感）----
    if p["orbit"]:
        ring = Image.new("L", (S, S), 0)
        rd = ImageDraw.Draw(ring)
        c = P(BASE) / 2.0
        rad = P(322)
        rd.ellipse((c - rad, c - rad, c + rad, c + rad),
                   outline=255, width=max(1, int(round(P(2.2)))))
        ra = np.asarray(ring, dtype=np.float32) / 255.0 * 0.070
        rgb = rgb * (1.0 - ra[..., None]) + 255.0 * ra[..., None]

    # ---- 辉光：先铺模糊光晕，再压锐利实体 ----
    if p["glow"]:
        for mask, col, amt, blur in (
            (chev, (0xC8, 0xEA, 0xFF), 0.34, 30),
            (bar, (0x35, 0xD8, 0xF2), 0.62, 34),
        ):
            b = Image.fromarray((mask * 255).astype(np.uint8))
            b = b.filter(ImageFilter.GaussianBlur(P(blur)))
            ba = np.asarray(b, dtype=np.float32) / 255.0
            rgb = rgb + ba[..., None] * np.array(col, np.float32) * amt

    glyph_col = (chev_col * chev[..., None]
                 + bar_col * bar[..., None] * (1.0 - chev[..., None]))
    rgb = rgb * (1.0 - gmask[..., None]) + glyph_col * gmask[..., None]

    # ---- 内描边：上亮下弱，做出玻璃切边 ----
    if p["rim"] > 0:
        inner = superellipse_mask(S, P(p["margin"]) + P(2.8))
        edge = np.clip(tile_box_mask - inner, 0.0, 1.0)
        yy = np.mgrid[0:S, 0:S][0].astype(np.float32) / S
        fade = 0.40 + 0.60 * (1.0 - yy)
        ea = edge * fade * p["rim"]
        rgb = rgb * (1.0 - ea[..., None]) + 255.0 * ea[..., None]

    rgb = np.clip(rgb, 0.0, 255.0)
    alpha = tile_box_mask            # 字形完全落在底片内，不需要并入 alpha
    out = np.dstack([rgb, alpha * 255.0]).astype(np.uint8)
    img = Image.fromarray(out, "RGBA").resize((BASE, BASE), Image.LANCZOS)
    return img, resid


# ----------------------------------------------------------------------------
# ICO 封装
# ----------------------------------------------------------------------------

def _dib_entry(img):
    """32bpp BGRA 的 DIB + 1bpp AND 掩码（Windows 兼容性最好）。"""
    w, h = img.size
    px = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    bgra = px[::-1][:, :, [2, 1, 0, 3]].tobytes()

    stride = ((w + 31) // 32) * 4
    and_mask = bytearray()
    for row in range(h - 1, -1, -1):
        bits = bytearray(stride)
        for x in range(w):
            if px[row, x, 3] == 0:
                bits[x >> 3] |= 0x80 >> (x & 7)
        and_mask += bits

    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(bgra), 0, 0, 0, 0)
    return header + bgra + bytes(and_mask)


def _png_entry(img):
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_ico(path, images):
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size, img in images:
        blob = _png_entry(img) if size >= 64 else _dib_entry(img)
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                   len(blob), offset))
        offset += len(blob)
        blobs.append(blob)

    with open(path, "wb") as fh:
        fh.write(struct.pack("<HHH", 0, 1, len(images)))
        for e in entries:
            fh.write(e)
        for b in blobs:
            fh.write(b)


def main():
    os.makedirs(ASSETS, exist_ok=True)

    print("[1/3] 渲染母版 ...")
    large, resid_l = render("large")
    small, resid_s = render("small")
    print("      大尺寸质心残差: dx=%+.3f dy=%+.3f (基准单位)" % resid_l)
    print("      小尺寸质心残差: dx=%+.3f dy=%+.3f" % resid_s)

    print("[2/3] 生成各尺寸 ...")
    images = []
    for size in ICO_SIZES:
        src = small if size <= 40 else large
        images.append((size, src.resize((size, size), Image.LANCZOS)))

    ico = os.path.join(ASSETS, "codex-tool-fix.ico")
    build_ico(ico, images)
    print("      ->", ico)

    png = os.path.join(ASSETS, "codex-tool-fix.png")
    large.resize((PNG_PREVIEW, PNG_PREVIEW), Image.LANCZOS).save(png)
    print("      ->", png)

    print("[3/3] 结构校验 ...")
    with open(ico, "rb") as fh:
        data = fh.read()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    ok = reserved == 0 and kind == 1 and count == len(ICO_SIZES)
    for i in range(count):
        w, h, _c, _r, planes, bits, nbytes, off = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        if not (planes == 1 and bits == 32 and h == w and off + nbytes <= len(data)):
            ok = False
    print("      条目数=%d  文件=%d 字节  %s"
          % (count, len(data), "ok" if ok else "BAD"))
    print()
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
