# -*- coding: utf-8 -*-
"""
图标验收（可量化，不依赖看图）。

上一版的度量有两处不严谨，这里修正：

1) 面积用 alpha > 40 统计 -> 在小尺寸下抗锯齿边缘占比极大，16px 会虚高到
   92%，看起来像"各尺寸体量不一致"。改用 alpha >= 200（实心区）统计，
   并同时报告 alpha > 40 的边缘带占比，两者分开看。

2) 质心用"亮度 > 全图最大值 72%"提取 -> 渐变字形里较暗的那部分会被排除，
   测出的质心必然偏向亮的一侧，与本生成器实际做的"质量质心居中"不同定义。
   改为用稳健阈值（字形亮度中位数与底色中位数的中点）提取完整字形。

判据：
  - 四角必须透明（不出界）
  - 字形/底色对比度 >= 4.5:1（WCAG AA）
  - 36px 及以下：V 形与光标块之间必须仍有背景间隙（不糊成一块）
  - 实心面积在 16..256 各档之间的极差 <= 8 个百分点
  - 质心偏移 <= 2.0%（按稳健阈值）
"""
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "assets", "codex-tool-fix.ico")

RAMP = " .:-=+*#%@"
CHECK_SIZES = [16, 20, 24, 32, 48, 256]


def load(size):
    im = Image.open(ICO)
    im.size = (size, size)
    return im.convert("RGBA")


def ascii_of(img, cols=None):
    w, h = img.size
    cols = cols or w
    rows = max(1, int(cols * h / w / 2.05))
    small = img.resize((cols, rows), Image.LANCZOS)
    px = np.asarray(small.convert("RGBA"), dtype=np.float32)
    lum = 0.2126 * px[:, :, 0] + 0.7152 * px[:, :, 1] + 0.0722 * px[:, :, 2]
    alpha = px[:, :, 3] / 255.0
    out = []
    for r in range(rows):
        line = []
        for c in range(cols):
            if alpha[r, c] < 0.35:
                line.append(" ")
            else:
                v = lum[r, c] / 255.0
                line.append(RAMP[min(len(RAMP) - 1,
                                     int(v * (len(RAMP) - 1) + 0.5))])
        out.append("".join(line))
    return out


def rel_lum(rgb):
    c = np.array(rgb, np.float32) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])


def contrast(a, b):
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def glyph_threshold(lum, alpha):
    """稳健阈值：字形亮度簇与底色簇之间的中点。"""
    vals = lum[alpha >= 200]
    if vals.size < 8:
        vals = lum[alpha > 40]
    if vals.size == 0:
        return 255.0
    hi = np.percentile(vals, 92)
    lo = np.percentile(vals, 30)
    if hi - lo < 12:                       # 分布太平，退化为固定比例
        return float(lo + (hi - lo) * 0.55)
    return float((hi + lo) / 2.0)


def analyse(size):
    im = load(size)
    px = np.asarray(im, dtype=np.float32)
    lum = 0.2126 * px[:, :, 0] + 0.7152 * px[:, :, 1] + 0.0722 * px[:, :, 2]
    alpha = px[:, :, 3]

    solid = alpha >= 200
    edge = (alpha > 40) & (alpha < 200)
    solid_ratio = solid.mean() * 100
    edge_ratio = edge.mean() * 100

    corners = [alpha[0, 0], alpha[0, -1], alpha[-1, 0], alpha[-1, -1]]
    corners_clear = all(c < 8 for c in corners)

    thr = glyph_threshold(lum, alpha)
    glyph = (lum >= thr) & (alpha >= 200)
    glyph_ratio = glyph.mean() * 100

    if glyph.any():
        gy, gx = np.nonzero(glyph)
        mid = (size - 1) / 2.0
        off_x = (gx.mean() - mid) / size * 100
        off_y = (gy.mean() - mid) / size * 100
    else:
        off_x = off_y = 0.0

    if solid.any():
        sv = lum[solid]
        # 底色取暗侧，字形取亮侧（都按稳健分位）
        bg = float(np.percentile(sv, 25))
        fg = float(np.percentile(sv, 97))
    else:
        bg = fg = 0.0

    return dict(size=size, solid=solid_ratio, edge=edge_ratio,
                glyph=glyph_ratio, corners=corners_clear,
                off_x=off_x, off_y=off_y, bg=bg, fg=fg,
                img=im, lum=lum, alpha=alpha, thr=thr)


def gap_between(r):
    lum, alpha, thr = r["lum"], r["alpha"], r["thr"]
    bright = (lum >= thr) & (alpha >= 200)
    if not bright.any():
        return 0
    colcount = bright.sum(axis=0)
    cols = np.nonzero(colcount)[0]
    if len(cols) == 0:
        return 0
    lo, hi = int(cols.min()), int(cols.max())
    best = run = 0
    for c in range(lo, hi + 1):
        if colcount[c] == 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def main():
    print("=" * 70)
    print(" 图标验收")
    print("=" * 70)

    rows = [analyse(s) for s in CHECK_SIZES]
    ok = True

    for r in rows:
        cr = contrast([r["fg"]] * 3, [r["bg"]] * 3)
        g = gap_between(r)
        r["cr"], r["gap"] = cr, g
        print("\n--- %dpx ---" % r["size"])
        print("  实心面积 (a>=200) : %5.1f%%" % r["solid"])
        print("  抗锯齿边缘带      : %5.1f%%" % r["edge"])
        print("  字形占画布        : %5.1f%%" % r["glyph"])
        print("  四角透明          : %s" % ("是" if r["corners"] else "否 <<< 出界"))
        print("  质心偏移          : dx=%+.2f%%  dy=%+.2f%%" % (r["off_x"], r["off_y"]))
        print("  字形/底 对比度    : %5.1f:1  %s"
              % (cr, "达标" if cr >= 4.5 else "偏低 <<<"))
        print("  V 与光标块间隙    : %d px  %s"
              % (g, "有分隔" if g >= 1 else "已粘连 <<<"))

        if not r["corners"]:
            ok = False
        if cr < 4.5:
            ok = False
        if r["size"] <= 36 and g < 1:
            ok = False
        if abs(r["off_x"]) > 2.0 or abs(r["off_y"]) > 2.0:
            ok = False

    solids = [r["solid"] for r in rows]
    spread = max(solids) - min(solids)
    print("\n" + "-" * 70)
    print(" 实心面积跨尺寸极差 : %.1f 个百分点 (%s)"
          % (spread, "一致" if spread <= 8.0 else "偏大 <<<"))
    if spread > 8.0:
        ok = False

    print("\n" + "=" * 70)
    print(" ASCII 预览 16px")
    print("=" * 70)
    for line in ascii_of(load(16), cols=16):
        print("  |" + line + "|")

    print("\n" + "=" * 70)
    print(" ASCII 预览 32px")
    print("=" * 70)
    for line in ascii_of(load(32), cols=32):
        print("  |" + line + "|")

    print("\n" + "=" * 70)
    print(" ASCII 预览 256px (48 列)")
    print("=" * 70)
    for line in ascii_of(load(256), cols=48):
        print("  |" + line + "|")

    print()
    print("RESULT:", "OK" if ok else "NEEDS WORK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
