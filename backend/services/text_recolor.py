"""
Text Recoloring Utility.

Recolors text to contrasting colors based on background brightness.
Used for non-SAM-expanded text regions in highlight rendering.
"""

import cv2
import numpy as np


def recolor_text_simple(crop_bgr: np.ndarray, strength: float = 0.65) -> np.ndarray:
    """
    Recolor text with neon fill + white inner stroke.
    Robust to:
      - dark text on light background
      - light text on dark background
      - bright highlight boxes behind text (prevents tinting the whole box)

    Notes:
      - No background dimming / no background plate is added.
      - Any glow is constrained to stay INSIDE the glyphs (no background touching).
      - Works best for "non-SAM-expanded" text crops where you want a crisp recolor.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return crop_bgr
    if crop_bgr.ndim != 3 or crop_bgr.shape[2] != 3:
        return crop_bgr
    if crop_bgr.shape[0] < 3 or crop_bgr.shape[1] < 3:
        return crop_bgr

    strength = float(np.clip(strength, 0.0, 2.0))

    img = crop_bgr.astype(np.float32)
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # Background luminance estimate
    bg_luminance = float(np.percentile(gray, 70))

    # Decide text polarity using tail heaviness around median
    med = float(np.median(gray))
    dark_score = float(np.sum(gray < (med - 20.0)))
    light_score = float(np.sum(gray > (med + 20.0)))
    light_text = light_score > dark_score  # True means text likely bright

    # Choose colors with readability in mind
    if bg_luminance > 150:
        # Light background: neon red fill + pink-ish accents
        core_color = np.array([30, 23, 255], dtype=np.float32)   # BGR neon red
        edge_color = np.array([147, 20, 255], dtype=np.float32)  # BGR hot pink
        fill_keep_bright = 0.0
    else:
        # Dark background: very bright fill (toward white) + cyan accents
        core_color = np.array([0, 255, 255], dtype=np.float32)   # BGR bright yellow
        edge_color = np.array([255, 255, 0], dtype=np.float32)   # BGR cyan
        fill_keep_bright = 0.70  # blend some white into fill for readability

    # Initial intensity-based mask (will be gated by edges to avoid highlighting boxes)
    if light_text:
        # Light text: emphasize bright pixels
        t = 170.0
        mask = (gray - t) / (255.0 - t)
    else:
        # Dark text: emphasize dark pixels
        t = 220.0
        mask = (t - gray) / t

    mask = np.clip(mask, 0.0, 1.0)
    mask = mask ** 0.45  # make it more decisive

    # --- Edge/gradient gating to avoid tinting bright highlight rectangles ---
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = cv2.magnitude(gx, gy)

    g_hi = float(np.percentile(grad, 95))
    if g_hi < 1e-6:
        return crop_bgr

    grad_n = np.clip(grad / g_hi, 0.0, 1.0)

    # Threshold: keep only "edgey" areas
    edge_gate = (grad_n > 0.20).astype(np.float32)

    # Expand gate slightly so letter interiors survive (not just outlines)
    edge_gate_u8 = (edge_gate * 255).astype(np.uint8)
    edge_gate_u8 = cv2.dilate(edge_gate_u8, np.ones((3, 3), np.uint8), iterations=1)
    edge_gate = edge_gate_u8.astype(np.float32) / 255.0

    mask = mask * edge_gate

    # Optional tiny dilation (keep minimal to avoid creeping into boxes)
    mask_u8 = (mask * 255).astype(np.uint8)
    mask_u8 = cv2.dilate(mask_u8, np.ones((2, 2), np.uint8), iterations=1)
    mask = mask_u8.astype(np.float32) / 255.0

    # Feather for smooth blending
    mask = cv2.GaussianBlur(mask, (3, 3), 0)

    # Binary mask for morphology-based stroke/glow
    m_bin = (mask > 0.35).astype(np.uint8) * 255
    m_bin = cv2.morphologyEx(m_bin, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8), iterations=1)

    # --- White inner stroke (inside the glyphs only) ---
    er = cv2.erode(m_bin, np.ones((2, 2), np.uint8), iterations=1)
    inner_stroke = cv2.subtract(m_bin, er).astype(np.float32) / 255.0
    inner_stroke = cv2.GaussianBlur(inner_stroke, (3, 3), 0)

    # --- Inner glow (also inside glyphs only, no background touching) ---
    # Use edges from the glyph, then dilate, then *AND* with glyph mask to constrain inside.
    edges = cv2.Canny(m_bin, 40, 120)
    glow = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    glow = cv2.GaussianBlur(glow, (7, 7), 0).astype(np.float32) / 255.0

    glyph = (m_bin.astype(np.float32) / 255.0)
    glow = glow * glyph  # constrain glow to inside the text only

    # Build constant tint images
    h, w = gray.shape
    text_tint = np.empty((h, w, 3), dtype=np.float32)
    text_tint[..., 0] = core_color[0]
    text_tint[..., 1] = core_color[1]
    text_tint[..., 2] = core_color[2]

    edge_tint = np.empty((h, w, 3), dtype=np.float32)
    edge_tint[..., 0] = edge_color[0]
    edge_tint[..., 1] = edge_color[1]
    edge_tint[..., 2] = edge_color[2]

    out = img.copy()
    white = np.full_like(out, 255.0)

    # 1) Inner glow tint (inside glyph only)
    glow_alpha = (0.2 * strength * glow)[..., None]
    out = out * (1.0 - glow_alpha) + edge_tint * glow_alpha

    # 2) White inner stroke (inside glyph only)
    stroke_alpha = (0.95 * strength * inner_stroke)[..., None]
    out = out * (1.0 - stroke_alpha) + white * stroke_alpha

    # 3) Fill recolor (inside glyph only via mask)
    neon_boost = 1.25
    dominant = np.clip(text_tint * neon_boost, 0.0, 255.0)

    # On dark backgrounds, blend fill toward white for readability
    if fill_keep_bright > 0.0:
        dominant = dominant * (1.0 - fill_keep_bright) + white * fill_keep_bright

    text_alpha = (1.0 * strength * mask)[..., None]
    out = out * (1.0 - text_alpha) + dominant * text_alpha

    return np.clip(out, 0, 255).astype(np.uint8)
