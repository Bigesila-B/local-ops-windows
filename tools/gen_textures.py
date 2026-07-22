#!/usr/bin/env python3
"""生成舱壁/面板纹理 → static/assets/

孔板：程序化生成严格周期的冲压孔板（螺距整除尺寸 → 绝对无缝、亮度均匀）。
拉丝金属：程序化频域各向异性噪声，天然周期且无平铺接缝。
重新生成：python3 tools/gen_textures.py
"""
import os

import numpy as np
from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "static", "assets")
os.makedirs(OUT, exist_ok=True)

rng = np.random.default_rng(20260722)


def smooth_noise(shape, cells, octaves=3, persistence=0.55):
    h, w = shape
    total = np.zeros(shape)
    amp, norm = 1.0, 0.0
    for o in range(octaves):
        c = max(2, int(cells * (2 ** o)))
        g = (rng.random((c, c)) * 255).astype(np.uint8)
        img = Image.fromarray(g).resize((w, h), Image.BICUBIC)
        total += amp * (np.asarray(img, dtype=float) / 255.0)
        norm += amp
        amp *= persistence
    total /= norm
    total -= total.min()
    total /= (total.max() + 1e-9)
    return total


def hole_plate(filename, base_rgb, depth, pitch=22, r=6.2):
    """严格周期的冲压孔板：尺寸 = 螺距整数倍 → 平铺无缝。

    depth: 孔深强度 0..1（暗色舱壁取大，浅色舱壁取小）。
    """
    S = pitch * 8
    y, x = np.mgrid[0:S, 0:S]
    base = np.array(base_rgb, float)
    img = np.ones((S, S, 3)) * base
    # 极淡的低频明暗（不破坏周期性：噪声场自身按 S 周期化）
    n = smooth_noise((S, S), 5, 3)
    # 把噪声场镜像成可平铺形式
    n = (n + np.fliplr(n) + np.flipud(n) + np.flipud(np.fliplr(n))) / 4.0
    img *= 1 + (n[..., None] - 0.5) * 0.05

    for cy in range(0, S, pitch):
        for cx in range(0, S, pitch):
            hx, hy = cx + pitch / 2, cy + pitch / 2
            d = np.hypot(x - hx, y - hy)
            hole = d < r
            # 孔内：上深下浅（冲压凹陷的受光）
            grad = np.clip((y - hy) / (r * 2) + 0.5, 0, 1)  # 0 顶 1 底
            inner = base[None, None, :] * (1 - depth) + base[None, None, :] * depth * 0.35 * grad[..., None]
            img[hole] = inner[hole]
            # 孔缘下侧反光（凸起亮边）
            rim = (d >= r) & (d < r + 1.4) & (y > hy)
            img[rim] = np.clip(img[rim] * 1.22, 0, 255)
            # 孔缘上侧投影
            rim2 = (d >= r) & (d < r + 1.1) & (y <= hy)
            img[rim2] = img[rim2] * (1 - depth * 0.35)

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    out.save(os.path.join(OUT, filename), "JPEG", quality=88, optimize=True)
    print("生成", filename, out.size)


def brushed(filename, base_rgb, size=1024, contrast=0.05, grain=0.012):
    """频域各向异性噪声 → 程序化拉丝金属。

    FFT 噪声天然周期 → 平铺绝对无缝、无镜像对称感、亮度均匀无"断层"。
    拉丝 = 水平方向只留低频（线条长而连续）、垂直方向保留宽频（线条细）。
    """
    noise = rng.standard_normal((size, size))
    F = np.fft.fft2(noise)
    fy = np.fft.fftfreq(size)[:, None]
    fx = np.fft.fftfreq(size)[None, :]
    # fx 窄通带（条纹沿水平拉长），fy 宽通带（条纹细）
    filt = np.exp(-(np.abs(fx) / 0.012) ** 2) * np.exp(-(np.abs(fy) / 0.28) ** 2)
    streak = np.real(np.fft.ifft2(F * filt))
    streak -= streak.min()
    streak /= streak.max() + 1e-9
    # 极细磨砂颗粒（白噪声自身即周期）
    g = rng.standard_normal((size, size))
    g /= np.abs(g).max() + 1e-9

    base = np.array(base_rgb, float)
    img = base[None, None, :] * (1 + (streak[..., None] - 0.5) * 2 * contrast + g[..., None] * grain)
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    out.save(os.path.join(OUT, filename), "JPEG", quality=90, optimize=True)
    print("生成", filename, out.size)


# 孔板（严格周期，平铺无缝）
hole_plate("deck-dark.jpg", (23, 27, 32), depth=0.62, pitch=10, r=2.4)
hole_plate("deck-light.jpg", (186, 192, 200), depth=0.16, pitch=10, r=2.4)

# 拉丝金属：频域周期噪声，平铺无缝无断层 + 深色版重派生
# 中灰基底：soft-light 混合下只出拉丝纹理、不改变面板明度
brushed("metal-brush.jpg", (128, 128, 128), contrast=0.16, grain=0.03)
brush = Image.open(os.path.join(OUT, "metal-brush.jpg")).convert("RGB")
tint = Image.new("RGB", brush.size, (43, 51, 61))
dark_brush = Image.blend(Image.eval(brush, lambda v: int(v * 0.32)), tint, 0.55)
dark_brush.save(os.path.join(OUT, "metal-brush-dark.jpg"), "JPEG", quality=86, optimize=True)
print("生成 metal-brush-dark.jpg", dark_brush.size)

print("完成 →", OUT)
