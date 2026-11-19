"""Adaptive per-LED color sampler.

The fast path is a Gaussian-weighted, trimmed mean in linear RGB. Complex
zones (hard colour boundaries, several significant hues, tiny bright objects)
switch to an OKLab histogram cluster or a bright-pixel mean. Everything is
vectorised across all LEDs; heavy OKLab work is done only for complex zones.
"""

from __future__ import annotations

import numpy as np

L_BINS, AB_BINS = 10, 8
BINS = L_BINS * AB_BINS * AB_BINS


def srgb_to_linear(x):
    x = np.asarray(x, dtype=np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x):
    x = np.clip(np.asarray(x, dtype=np.float32), 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055) * 255.0


def srgb_to_oklab(rgb):
    rgb = np.asarray(rgb, dtype=np.float32) / 255.0
    lin = srgb_to_linear(rgb * 255.0)
    l = 0.4122214708 * lin[..., 0] + 0.5363325363 * lin[..., 1] + 0.0514459929 * lin[..., 2]
    m = 0.2119034982 * lin[..., 0] + 0.6806995451 * lin[..., 1] + 0.1073969566 * lin[..., 2]
    s = 0.0883024619 * lin[..., 0] + 0.2817188376 * lin[..., 1] + 0.6299787005 * lin[..., 2]
    l = np.cbrt(np.maximum(l, 1e-9))
    m = np.cbrt(np.maximum(m, 1e-9))
    s = np.cbrt(np.maximum(s, 1e-9))
    return np.stack([
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    ], axis=-1)


def oklab_to_srgb(lab):
    lab = np.asarray(lab, dtype=np.float32)
    l_ = lab[..., 0] + 0.3963377774 * lab[..., 1] + 0.2158037573 * lab[..., 2]
    m_ = lab[..., 0] - 0.1055613458 * lab[..., 1] - 0.0638541728 * lab[..., 2]
    s_ = lab[..., 0] - 0.0894841775 * lab[..., 1] - 1.2914855480 * lab[..., 2]
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    lin = np.stack([
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    ], axis=-1)
    return linear_to_srgb(lin)


class AdaptiveSampler:
    def __init__(self, boxes, frame_w, frame_h, trim=0.05):
        self.boxes = list(boxes)
        self.frame_w, self.frame_h = frame_w, frame_h
        self.trim = trim
        self.n = len(self.boxes)
        self.h = max(1, max(y1 - y0 for y0, y1, x0, x1 in self.boxes)) if self.boxes else 1
        self.w = max(1, max(x1 - x0 for y0, y1, x0, x1 in self.boxes)) if self.boxes else 1
        self.patches = np.zeros((self.n, self.h, self.w, 3), np.uint8)
        self.weights = np.zeros((self.n, self.h, self.w), np.float32)
        for i, (y0, y1, x0, x1) in enumerate(self.boxes):
            cy = (y0 + y1 - 1) / 2.0
            cx = (x0 + x1 - 1) / 2.0
            yy, xx = np.mgrid[y0:y1, x0:x1]
            sigma = max(1.5, min(y1 - y0, x1 - x0) / 2.6)
            w = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2.0 * sigma * sigma))
            self.weights[i, : y1 - y0, : x1 - x0] = w.astype(np.float32)

    def _fill(self, frame, clip_rect=None, bgra=False):
        for i, (y0, y1, x0, x1) in enumerate(self.boxes):
            patch = frame[y0:y1, x0:x1]
            if bgra:
                patch = patch[..., 2::-1]
            self.patches[i, : y1 - y0, : x1 - x0] = patch
        if clip_rect is None:
            return self.weights
        top, bottom, left, right = (int(v) for v in clip_rect)
        weights = self.weights.copy()
        for i, (y0, y1, x0, x1) in enumerate(self.boxes):
            row0 = max(0, min(self.h, top - y0))
            row1 = max(0, min(self.h, bottom - y0))
            col0 = max(0, min(self.w, left - x0))
            col1 = max(0, min(self.w, right - x0))
            if row0:
                weights[i, :row0, :] = 0
            if row1 < self.h:
                weights[i, row1:, :] = 0
            if col0:
                weights[i, :, :col0] = 0
            if col1 < self.w:
                weights[i, :, col1:] = 0
        return weights

    def sample(self, frame, clip_rect=None, bgra=False):
        if not self.boxes:
            return np.zeros((0, 3), np.float32)
        weights = self._fill(frame, clip_rect, bgra=bgra)
        n, h, w = self.n, self.h, self.w
        patches = self.patches.astype(np.float32)
        wsum = weights.sum(axis=(1, 2), keepdims=True) + 1e-9
        wsum_1 = wsum[:, 0, 0][:, None]
        lin = srgb_to_linear(patches)

        luma = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
        flat_l = luma.reshape(n, -1)
        lo = np.quantile(flat_l, self.trim, axis=1)[:, None, None]
        hi = np.quantile(flat_l, 1 - self.trim, axis=1)[:, None, None]
        keep = ((luma >= lo) & (luma <= hi)).astype(np.float32)
        ww = weights * keep
        mean_lin = (lin * ww[..., None]).sum(axis=(1, 2)) / np.maximum(wsum_1, 1e-9)
        mean_rgb = linear_to_srgb(mean_lin)

        lab = srgb_to_oklab(patches.reshape(-1, 3)).reshape(n, h, w, 3)
        mean_lab = (lab * weights[..., None]).sum(axis=(1, 2)) / wsum_1[:, 0][:, None]
        var = (((lab - mean_lab[:, None, None, :]) ** 2) * weights[..., None]).sum(axis=(1, 2)) / wsum_1[:, 0][:, None]
        spread = np.sqrt(np.maximum(0.0, var[:, 1] + var[:, 2]))
        lmean = (luma * weights).sum(axis=(1, 2)) / wsum_1[:, 0]

        # small bright objects: use the bright pixels instead of mixing them with the background
        lmax = luma.max(axis=(1, 2))
        threshold = np.maximum(0.18, lmax * 0.55)[:, None, None]
        bright_mask = (luma >= threshold).astype(np.float32) * weights
        bright_mass = bright_mask.sum(axis=(1, 2), keepdims=True)
        frac = bright_mass[:, 0, 0] / wsum_1[:, 0]
        bright_lin = (lin * bright_mask[..., None]).sum(axis=(1, 2)) / np.maximum(
            bright_mass[:, 0, 0][:, None], 1e-9
        )
        bright_rgb = linear_to_srgb(bright_lin)

        use_bright = (lmax > 0.2) & (lmax > lmean * 2.2) & (frac < 0.25) & (spread > 0.035)
        use_hist = spread > 0.055
        out = mean_rgb.copy()

        # OKLab histogram only for complex zones
        hist_idx = np.where(use_hist & ~use_bright)[0]
        if hist_idx.size:
            sub_lab = lab[hist_idx]
            sub_w = weights[hist_idx]
            qi = np.empty(sub_lab.shape, np.int16)
            qi[..., 0] = np.clip((sub_lab[..., 0] * L_BINS).astype(np.int16), 0, L_BINS - 1)
            qi[..., 1] = np.clip(((sub_lab[..., 1] + 0.4) / 0.8 * AB_BINS).astype(np.int16), 0, AB_BINS - 1)
            qi[..., 2] = np.clip(((sub_lab[..., 2] + 0.4) / 0.8 * AB_BINS).astype(np.int16), 0, AB_BINS - 1)
            code = (qi[..., 0] * AB_BINS + qi[..., 1]) * AB_BINS + qi[..., 2]
            k = hist_idx.size
            offsets = np.arange(k, dtype=np.int64)[:, None, None] * BINS + code
            off = offsets.reshape(-1)
            counts = np.bincount(off, weights=sub_w.reshape(-1), minlength=k * BINS).reshape(k, BINS).astype(np.float32)
            sums = []
            for c in range(3):
                sums.append(
                    np.bincount(
                        off,
                        weights=(sub_lab[..., c] * sub_w).reshape(-1),
                        minlength=k * BINS,
                    ).reshape(k, BINS)
                )
            best = counts.argmax(axis=1)
            idx = np.arange(k)
            mass = counts[idx, best]
            hist_lab = np.stack(
                [sums[c][idx, best] / np.maximum(mass, 1e-9) for c in range(3)],
                axis=1,
            )
            out[hist_idx] = oklab_to_srgb(hist_lab)

        out[use_bright] = bright_rgb[use_bright]
        return np.clip(out, 0, 255)
