#!/usr/bin/env python3
"""
generate_previews.py — App Store Preview Generator

Composites Fastlane screenshots into framed App Store preview cards
with titles, subtitles, device frames, and background images.

Layout per card (top → bottom):
  ① Title text   (centered, configurable font)
  ② Subtitle text (centered, configurable font)
  ③ iPhone mockup (phone.png + screenshot + notch_camera.png)

All cards for a locale share one panoramic background slice.

Usage:
    python3 generate_previews.py --locale en-US
    python3 generate_previews.py --all
    python3 generate_previews.py --all --screen-region 32,12,736,1600

Assets (place in this directory):
    banner.jpg / banner.png   — wide background image (optional; uses gradient if absent)
    phone.png                 — iPhone silhouette (black, RGBA, 800×1630)
    notch_camera.png          — Dynamic Island overlay (RGBA, 800×1630)
    config.json               — global layout + typography settings
    metadata/{locale}.json    — per-locale titles, subtitles, optional overrides

Directory layout:
    generate_previews.py
    config.json
    phone.png
    notch_camera.png
    banner.jpg                (optional)
    metadata/
        en-US.json
        de-DE.json
        ...
    screenshots/
        en-US/
            iPhone 16 Pro Max-01_Dashboard.png
            ...
        de-DE/
            ...
    output/                   (generated)
        en-US/
            01_Dashboard.png
            ...

Requirements:
    pip install Pillow
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ─── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
METADATA_DIR = SCRIPT_DIR / "metadata"
OUTPUT_DIR   = SCRIPT_DIR / "output"
SS_DIR       = SCRIPT_DIR / "screenshots"


def discover_locales() -> list[str]:
    """Auto-discover locales from metadata/*.json filenames."""
    if not METADATA_DIR.exists():
        return []
    return sorted(p.stem for p in METADATA_DIR.glob("*.json"))

# ─── Card dimensions ──────────────────────────────────────────────────────────

CARD_W      = 1320   # iPhone 6.9"  (App Store required)
CARD_H      = 2868
CARD_W_IPAD = 2064   # iPad Pro 13" (App Store required)
CARD_H_IPAD = 2752

# ─── Avenir Next TTC font index map ───────────────────────────────────────────

AVENIR_TTC = "/System/Library/Fonts/Avenir Next.ttc"
AVENIR_WEIGHTS = {
    "Bold":             0,
    "Bold Italic":      1,
    "Demi Bold":        2,
    "Demi Bold Italic": 3,
    "Italic":           4,
    "Medium":           5,
    "Medium Italic":    6,
    "Regular":          7,
    "Heavy":            8,
    "Heavy Italic":     9,
    "Ultra Light":      10,
    "Ultra Light Italic": 11,
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_color(hex_str: str, alpha: int = 255) -> tuple:
    """Parse '#RRGGBB' into (R, G, B, A)."""
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, alpha)


def load_font(family: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font by family + weight name. Falls back to default on failure."""
    if family in ("Avenir Next", "AvenirNext", "Avenir"):
        idx = AVENIR_WEIGHTS.get(weight, 0)
        try:
            return ImageFont.truetype(AVENIR_TTC, size, index=idx)
        except Exception:
            pass
    # Generic fallback: try the weight as a filename stem
    for candidate in [
        f"/System/Library/Fonts/{family}.ttf",
        f"/Library/Fonts/{family} {weight}.ttf",
        f"/Library/Fonts/{family}-{weight}.ttf",
    ]:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            pass
    return ImageFont.load_default(size=size)


def make_gradient(top_color: str, bottom_color: str, w: int, h: int) -> Image.Image:
    """Generate a vertical gradient image of size w×h."""
    top = parse_color(top_color)
    bot = parse_color(bottom_color)
    img = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        draw.line([(0, y), (w - 1, y)], fill=(r, g, b, 255))
    return img


def get_notch_bounds(notch_img: Image.Image) -> tuple | None:
    """Return (x, y, w, h) of the Dynamic Island content in notch image coordinates."""
    alpha = notch_img.split()[3]
    bbox = alpha.getbbox()   # bounding box of all non-transparent pixels
    if bbox is None:
        return None
    x, y, x2, y2 = bbox
    return (x, y, x2 - x, y2 - y)


def draw_dynamic_island(card: Image.Image, notch_bounds: tuple,
                         phone_x: int, phone_y: int, scale: float) -> None:
    """Draw a solid black rounded-rect pill for the Dynamic Island fill."""
    nb_x, nb_y, nb_w, nb_h = notch_bounds
    di_x = phone_x + int(nb_x * scale)
    di_y = phone_y + int(nb_y * scale)
    di_w = int(nb_w * scale)
    di_h = int(nb_h * scale)
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        [(di_x, di_y), (di_x + di_w, di_y + di_h)],
        radius=di_h // 2,
        fill=(0, 0, 0, 255),
    )


def apply_rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply smooth anti-aliased rounded corners via 4× supersampling."""
    img = img.convert("RGBA")
    w, h = img.size
    # Draw mask at 4× resolution, then scale down — gives smooth edges
    scale = 4
    mask_large = Image.new("L", (w * scale, h * scale), 0)
    draw = ImageDraw.Draw(mask_large)
    draw.rounded_rectangle(
        [(0, 0), (w * scale - 1, h * scale - 1)],
        radius=radius * scale,
        fill=255,
    )
    mask = mask_large.resize((w, h), Image.LANCZOS)
    result = img.copy()
    result.putalpha(mask)
    return result


def scale_to_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale + center-crop image to exactly w×h."""
    src_r = img.width / img.height
    tgt_r = w / h
    if src_r > tgt_r:
        new_h, new_w = h, int(img.width * h / img.height)
    else:
        new_w, new_h = w, int(img.height * w / img.width)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    cx = (new_w - w) // 2
    cy = (new_h - h) // 2
    return img.crop((cx, cy, cx + w, cy + h))



def draw_text_block(
    card: Image.Image,
    text: str,
    y: int,
    font: ImageFont.FreeTypeFont,
    color: tuple,
    shadow: bool,
    shadow_color: tuple,
    shadow_blur: int,
    card_w: int,
    spacing: int,
    cfg: dict,
) -> int:
    """Draw centered multi-line text using Pillow's built-in multiline renderer."""
    # Measure at (0, 0) to find block width, then offset to center on card
    dummy = ImageDraw.Draw(card)
    bbox = dummy.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
    x = (card_w - (bbox[2] - bbox[0])) // 2 - bbox[0]

    if shadow:
        shadow_layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow_layer).multiline_text(
            (x, y), text, font=font, fill=shadow_color,
            spacing=spacing, align="center",
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        card.alpha_composite(shadow_layer)

    ImageDraw.Draw(card).multiline_text(
        (x, y), text, font=font, fill=color,
        spacing=spacing, align="center",
    )

    bbox_at_y = dummy.multiline_textbbox((x, y), text, font=font, spacing=spacing, align="center")
    return bbox_at_y[3]


def add_phone_shadow(card: Image.Image, phone: Image.Image,
                     px: int, py: int,
                     blur: int = 28, offset: tuple = (10, 18), alpha: int = 130) -> None:
    """Composite a blurred drop shadow under the phone."""
    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", phone.size, (0, 0, 0, alpha))
    shadow.putalpha(phone.split()[3].point(lambda a: alpha if a > 0 else 0))
    ox, oy = offset
    layer.paste(shadow, (px + ox, py + oy), shadow)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=blur))
    card.alpha_composite(layer)

# ─── Preview sheet ────────────────────────────────────────────────────────────

def create_preview_sheet(locale: str, gap: int = 20, thumb_w: int = 400) -> None:
    """Render all output cards for a locale side-by-side into a single preview image."""
    out_dir = OUTPUT_DIR / locale
    cards = sorted(p for p in out_dir.glob("*.png")
                   if p.stem != "preview_sheet" and not p.stem.startswith("iPad"))
    if not cards:
        return

    # Scale factor from original card width
    scale = thumb_w / CARD_W
    thumb_h = int(CARD_H * scale)

    pad = gap  # padding on all outer edges too
    total_w = pad + len(cards) * thumb_w + (len(cards) - 1) * gap + pad
    total_h = pad + thumb_h + pad

    sheet = Image.new("RGB", (total_w, total_h), (30, 30, 30))

    for i, card_path in enumerate(cards):
        img = Image.open(card_path).convert("RGB")
        img = img.resize((thumb_w, thumb_h), Image.LANCZOS)
        x = pad + i * (thumb_w + gap)
        sheet.paste(img, (x, pad))

    out_path = OUTPUT_DIR / f"preview_sheet_{locale}.png"
    sheet.save(str(out_path), "PNG", optimize=True)
    print(f"    ✓ preview sheet → {out_path.relative_to(SCRIPT_DIR)}")


# ─── Per-locale processor ─────────────────────────────────────────────────────

def process_locale(
    locale: str,
    panoramic_bg: Image.Image,
    phone_img: Image.Image,
    notch_img: Image.Image | None,
    cfg: dict,
    screen_region: tuple,
):
    meta_path = METADATA_DIR / f"{locale}.json"
    ss_dir    = SS_DIR / locale

    if not meta_path.exists():
        print(f"  [skip] no metadata: {meta_path.name}")
        return
    if not ss_dir.exists():
        print(f"  [skip] no screenshots dir: {ss_dir}")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    screens = meta["screenshots"]

    # Map "01_Dashboard" → Path
    ss_map: dict[str, Path] = {}
    for p in sorted(ss_dir.glob("*.png")):
        key = p.stem.split("-", 1)[-1]
        ss_map[key] = p

    # ── Phone geometry ─────────────────────────────────────────────────────────
    layout      = cfg["layout"]
    phone_scale = layout["phone_scale"]
    bottom_pad  = layout["phone_bottom_pad"]

    phone_w = int(CARD_W * phone_scale)
    phone_h = int(phone_w * phone_img.height / phone_img.width)
    phone_x = (CARD_W - phone_w) // 2
    phone_y = CARD_H - phone_h - bottom_pad

    frame        = phone_img.resize((phone_w, phone_h), Image.LANCZOS)
    notch        = notch_img.resize((phone_w, phone_h), Image.LANCZOS) if notch_img else None
    notch_bounds = get_notch_bounds(notch_img) if notch_img else None
    frame_scale  = phone_w / phone_img.width

    # Screen region scaled from original frame coords
    orig_w        = phone_img.width
    scale_f       = phone_w / orig_w
    sx, sy, sw, sh = screen_region
    scr_x = int(sx * scale_f)
    scr_y = int(sy * scale_f)
    scr_w = int(sw * scale_f)
    scr_h = int(sh * scale_f)
    corner_ratio = layout.get("screen_corner_radius_ratio", 0.075)

    # ── Default fonts + colors ─────────────────────────────────────────────────
    def_title_font = load_font(
        cfg["title_font"]["family"],
        cfg["title_font"]["weight"],
        cfg["title_font"]["size"],
    )
    def_sub_font = load_font(
        cfg["subtitle_font"]["family"],
        cfg["subtitle_font"]["weight"],
        cfg["subtitle_font"]["size"],
    )
    def_title_color = parse_color(cfg["title_color"])
    def_sub_color   = parse_color(cfg["subtitle_color"])
    shadow_enabled  = cfg.get("text_shadow", True)
    shadow_color    = parse_color(cfg.get("text_shadow_color", "#FFFFFF"))
    shadow_blur     = cfg.get("text_shadow_blur", 18)
    title_sub_gap   = layout["title_subtitle_gap"]
    text_top_pad    = layout["text_top_pad"]
    show_subtitle   = cfg.get("show_subtitle", True)

    out_dir = OUTPUT_DIR / locale
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, sc in enumerate(screens):
        key      = sc["file"]
        title    = sc.get("title", "")
        subtitle = sc.get("subtitle", "")

        # Per-screenshot font/color overrides
        t_font = (
            load_font(
                sc.get("title_font_family", cfg["title_font"]["family"]),
                sc.get("title_font_weight", cfg["title_font"]["weight"]),
                sc.get("title_font_size",   cfg["title_font"]["size"]),
            )
            if any(k in sc for k in ("title_font_family", "title_font_weight", "title_font_size"))
            else def_title_font
        )
        s_font = (
            load_font(
                sc.get("subtitle_font_family", cfg["subtitle_font"]["family"]),
                sc.get("subtitle_font_weight", cfg["subtitle_font"]["weight"]),
                sc.get("subtitle_font_size",   cfg["subtitle_font"]["size"]),
            )
            if any(k in sc for k in ("subtitle_font_family", "subtitle_font_weight", "subtitle_font_size"))
            else def_sub_font
        )
        t_color = parse_color(sc["title_color"])    if "title_color"    in sc else def_title_color
        s_color = parse_color(sc["subtitle_color"]) if "subtitle_color" in sc else def_sub_color

        ss_path = ss_map.get(key)
        if ss_path is None:
            print(f"    [warn] {locale}: no screenshot for '{key}'")
            continue

        # ── Build card ─────────────────────────────────────────────────────────
        card = Image.new("RGBA", (CARD_W, CARD_H))
        card.paste(panoramic_bg.crop((i * CARD_W, 0, (i + 1) * CARD_W, CARD_H)))

        # Overlay to mute background and improve text readability
        overlay_opacity = cfg.get("bg_overlay_opacity", 0)
        if overlay_opacity > 0:
            alpha = int(overlay_opacity / 100 * 255)
            overlay_hex = cfg.get("bg_overlay_color", "#000000")
            overlay_rgb = parse_color(overlay_hex)[:3]
            overlay = Image.new("RGBA", (CARD_W, CARD_H), (*overlay_rgb, alpha))
            card.alpha_composite(overlay)

        # Phone shadow
        add_phone_shadow(card, frame, phone_x, phone_y)

        # Phone silhouette (behind screenshot)
        card.alpha_composite(frame, (phone_x, phone_y))

        # Screenshot into screen region
        corner_radius = int(scr_w * corner_ratio)
        ss = Image.open(ss_path).convert("RGBA").resize((scr_w, scr_h), Image.LANCZOS)
        ss = apply_rounded_corners(ss, corner_radius)
        card.alpha_composite(ss, (phone_x + scr_x, phone_y + scr_y))

        # Dynamic Island notch overlay
        if notch:
            card.alpha_composite(notch, (phone_x, phone_y))

        # ── Text ──────────────────────────────────────────────────────────────
        y = text_top_pad

        if title:
            title_lead = cfg.get("title_line_extra", int(t_font.size * 0.15))
            y = draw_text_block(
                card, title, y, t_font, t_color,
                shadow_enabled, shadow_color, shadow_blur,
                CARD_W, title_lead, cfg,
            )
            y += title_sub_gap

        if subtitle and show_subtitle:
            sub_lead = int(s_font.size * 0.12)
            draw_text_block(
                card, subtitle, y, s_font, s_color,
                shadow_enabled, shadow_color, shadow_blur,
                CARD_W, sub_lead, cfg,
            )

        # ── Save ──────────────────────────────────────────────────────────────
        out_path = out_dir / f"{key}.png"
        card.convert("RGB").save(str(out_path), "PNG", optimize=True)
        print(f"    ✓ {out_path.relative_to(SCRIPT_DIR)}")

    create_preview_sheet(locale)

# ─── iPad locale processor ────────────────────────────────────────────────────

def process_locale_ipad(
    locale: str,
    panoramic_bg: Image.Image,
    ipad_img: Image.Image | None,
    cfg: dict,
):
    """Generate iPad Pro 13" preview cards (2064×2752).

    If ipad.png is present in the script directory it is used as a device frame
    (same compositing logic as the iPhone). Otherwise the screenshot is placed
    directly on the background with rounded corners — no device silhouette.
    """
    meta_path = METADATA_DIR / f"{locale}.json"
    ss_dir    = SS_DIR / locale

    if not meta_path.exists():
        print(f"  [skip iPad] no metadata: {meta_path.name}")
        return
    if not ss_dir.exists():
        print(f"  [skip iPad] no screenshots dir: {ss_dir}")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    screens = meta["screenshots"]

    # Map screenshot key → path for iPad files only ("iPad Pro …-01_Dashboard.png")
    ss_map: dict[str, Path] = {}
    for p in sorted(ss_dir.glob("iPad*.png")):
        key = p.stem.split("-", 1)[-1] if "-" in p.stem else p.stem
        ss_map[key] = p

    if not ss_map:
        print(f"  [skip iPad] no iPad screenshots found in {ss_dir}")
        return

    ipad_cfg = cfg.get("ipad_layout", {})

    text_top_pad  = ipad_cfg.get("text_top_pad", 220)
    title_sub_gap = ipad_cfg.get("title_subtitle_gap", 40)
    ss_top_pad    = ipad_cfg.get("screenshot_top_pad", 460)
    ss_side_pad   = ipad_cfg.get("screenshot_side_pad", 40)
    ss_bot_pad    = ipad_cfg.get("screenshot_bottom_pad", 80)
    corner_radius = ipad_cfg.get("screen_corner_radius", 50)

    # Scale fonts proportionally to iPad card width
    scale = CARD_W_IPAD / CARD_W

    def_title_font = load_font(
        cfg["title_font"]["family"],
        cfg["title_font"]["weight"],
        int(cfg["title_font"]["size"] * scale),
    )
    def_sub_font = load_font(
        cfg["subtitle_font"]["family"],
        cfg["subtitle_font"]["weight"],
        int(cfg["subtitle_font"]["size"] * scale),
    )
    def_title_color = parse_color(cfg["title_color"])
    def_sub_color   = parse_color(cfg["subtitle_color"])
    shadow_enabled  = cfg.get("text_shadow", True)
    shadow_color    = parse_color(cfg.get("text_shadow_color", "#FFFFFF"))
    shadow_blur     = cfg.get("text_shadow_blur", 18)
    show_subtitle   = cfg.get("show_subtitle", True)

    # Optional iPad device frame (ipad.png alongside phone.png)
    if ipad_img is not None:
        frame_w = int(CARD_W_IPAD * 0.88)
        frame_h = int(frame_w * ipad_img.height / ipad_img.width)
        frame   = ipad_img.resize((frame_w, frame_h), Image.LANCZOS)
        frame_x = (CARD_W_IPAD - frame_w) // 2
        frame_y = CARD_H_IPAD - frame_h + 100
    else:
        frame = frame_x = frame_y = None

    out_dir = OUTPUT_DIR / locale
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, sc in enumerate(screens):
        key      = sc["file"]
        title    = sc.get("title", "")
        subtitle = sc.get("subtitle", "")

        # Per-screenshot font / color overrides (mirror iPhone logic)
        t_font = (
            load_font(
                sc.get("title_font_family", cfg["title_font"]["family"]),
                sc.get("title_font_weight", cfg["title_font"]["weight"]),
                int(sc.get("title_font_size", cfg["title_font"]["size"]) * scale),
            )
            if any(k in sc for k in ("title_font_family", "title_font_weight", "title_font_size"))
            else def_title_font
        )
        s_font = (
            load_font(
                sc.get("subtitle_font_family", cfg["subtitle_font"]["family"]),
                sc.get("subtitle_font_weight", cfg["subtitle_font"]["weight"]),
                int(sc.get("subtitle_font_size", cfg["subtitle_font"]["size"]) * scale),
            )
            if any(k in sc for k in ("subtitle_font_family", "subtitle_font_weight", "subtitle_font_size"))
            else def_sub_font
        )
        t_color = parse_color(sc["title_color"])    if "title_color"    in sc else def_title_color
        s_color = parse_color(sc["subtitle_color"]) if "subtitle_color" in sc else def_sub_color

        ss_path = ss_map.get(key)
        if ss_path is None:
            print(f"    [warn] {locale}: no iPad screenshot for '{key}'")
            continue

        # ── Build card ─────────────────────────────────────────────────────────
        card = Image.new("RGBA", (CARD_W_IPAD, CARD_H_IPAD))
        card.paste(panoramic_bg.crop((i * CARD_W_IPAD, 0, (i + 1) * CARD_W_IPAD, CARD_H_IPAD)))

        overlay_opacity = cfg.get("bg_overlay_opacity", 0)
        if overlay_opacity > 0:
            alpha       = int(overlay_opacity / 100 * 255)
            overlay_rgb = parse_color(cfg.get("bg_overlay_color", "#000000"))[:3]
            overlay     = Image.new("RGBA", (CARD_W_IPAD, CARD_H_IPAD), (*overlay_rgb, alpha))
            card.alpha_composite(overlay)

        if frame is not None:
            # ── With iPad device frame (ipad.png) ──────────────────────────────
            add_phone_shadow(card, frame, frame_x, frame_y)
            card.alpha_composite(frame, (frame_x, frame_y))
            ss_area_x = frame_x + ss_side_pad
            ss_area_y = frame_y + ss_top_pad
            ss_area_w = frame_w - 2 * ss_side_pad
            ss_area_h = frame_h - ss_top_pad - ss_bot_pad
        else:
            # ── Frameless: screenshot floats on background ─────────────────────
            ss_area_x = ss_side_pad
            ss_area_y = ss_top_pad
            ss_area_w = CARD_W_IPAD - 2 * ss_side_pad
            ss_area_h = CARD_H_IPAD - ss_top_pad - ss_bot_pad

        ss = Image.open(ss_path).convert("RGBA").resize(
            (ss_area_w, ss_area_h), Image.LANCZOS
        )
        ss = apply_rounded_corners(ss, corner_radius)
        card.alpha_composite(ss, (ss_area_x, ss_area_y))

        # ── Text ──────────────────────────────────────────────────────────────
        y = text_top_pad
        if title:
            title_lead = cfg.get("title_line_extra", int(t_font.size * 0.15))
            y = draw_text_block(
                card, title, y, t_font, t_color,
                shadow_enabled, shadow_color, shadow_blur,
                CARD_W_IPAD, title_lead, cfg,
            )
            y += title_sub_gap
        if subtitle and show_subtitle:
            sub_lead = int(s_font.size * 0.12)
            draw_text_block(
                card, subtitle, y, s_font, s_color,
                shadow_enabled, shadow_color, shadow_blur,
                CARD_W_IPAD, sub_lead, cfg,
            )

        # ── Save — prefix with "iPad_" so filenames don't clash with iPhone ────
        out_path = out_dir / f"iPad_{key}.png"
        card.convert("RGB").save(str(out_path), "PNG", optimize=True)
        print(f"    ✓ {out_path.relative_to(SCRIPT_DIR)}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    available = discover_locales()

    parser = argparse.ArgumentParser(
        description="Generate framed App Store preview images from Fastlane screenshots."
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--locale", help="Process a single locale (e.g. en-US)")
    grp.add_argument("--all", action="store_true",  help="Process all locales")
    parser.add_argument(
        "--screen-region", metavar="X,Y,W,H",
        help="Override iPhone screen region in frame image coords (default from config.json)",
    )
    args = parser.parse_args()

    if args.locale and args.locale not in available:
        print(f"ERROR: locale '{args.locale}' not found. Available: {', '.join(available)}")
        sys.exit(1)

    # ── Load config ───────────────────────────────────────────────────────────
    config_path = SCRIPT_DIR / "config.json"
    if not config_path.exists():
        print("ERROR: config.json not found.")
        sys.exit(1)
    with open(config_path) as f:
        cfg = json.load(f)

    # ── Screen region (iPhone only) ───────────────────────────────────────────
    if args.screen_region:
        try:
            screen_region = tuple(map(int, args.screen_region.split(",")))
            assert len(screen_region) == 4
        except Exception:
            print("ERROR: --screen-region must be four integers: X,Y,W,H")
            sys.exit(1)
    else:
        screen_region = tuple(cfg["layout"]["screen_region"])

    gen_ipad = cfg.get("generate_ipad_previews", False)

    # ── Load iPhone assets ────────────────────────────────────────────────────
    phone_path = SCRIPT_DIR / "phone.png"
    notch_path = SCRIPT_DIR / "notch_camera.png"
    if not phone_path.exists():
        print(f"ERROR: phone.png not found in {SCRIPT_DIR}")
        sys.exit(1)
    phone_img = Image.open(phone_path).convert("RGBA")
    notch_img = Image.open(notch_path).convert("RGBA") if notch_path.exists() else None

    # ── Load optional iPad device frame ───────────────────────────────────────
    ipad_img = None
    if gen_ipad:
        ipad_frame_path = SCRIPT_DIR / "ipad.png"
        if ipad_frame_path.exists():
            print(f"Using iPad frame: ipad.png")
            ipad_img = Image.open(ipad_frame_path).convert("RGBA")

    # ── Locales and screenshot count ──────────────────────────────────────────
    locales = available if args.all else [args.locale]

    n_screens = 0
    for loc in locales:
        p = METADATA_DIR / f"{loc}.json"
        if p.exists():
            with open(p) as f:
                n_screens = len(json.load(f)["screenshots"])
            break
    if n_screens == 0:
        print("ERROR: No valid metadata found.")
        sys.exit(1)

    # ── Load background image ─────────────────────────────────────────────────
    bg_image = None
    banner_file = cfg.get("banner_file", None)
    banner_names = [banner_file] if banner_file else ["banner.jpg", "banner.png", "background.jpg", "background.png"]
    for name in banner_names:
        candidate = SCRIPT_DIR / name
        if candidate.exists():
            print(f"Using background: {name}")
            bg_image = Image.open(candidate).convert("RGBA")
            break

    grad = cfg.get("gradient_fallback", {"top_color": "#FF5C00", "bottom_color": "#CC3300"})

    # ── Process iPhone ────────────────────────────────────────────────────────
    total_w_iphone = CARD_W * n_screens
    if bg_image is not None:
        panoramic_iphone = scale_to_cover(bg_image, total_w_iphone, CARD_H)
    else:
        print(f"No banner found — using gradient ({grad['top_color']} → {grad['bottom_color']})")
        panoramic_iphone = make_gradient(grad["top_color"], grad["bottom_color"], total_w_iphone, CARD_H)

    for locale in locales:
        print(f"\nProcessing iPhone {locale}...")
        process_locale(locale, panoramic_iphone, phone_img, notch_img, cfg, screen_region)

    # ── Process iPad ──────────────────────────────────────────────────────────
    if gen_ipad:
        total_w_ipad = CARD_W_IPAD * n_screens
        if bg_image is not None:
            panoramic_ipad = scale_to_cover(bg_image, total_w_ipad, CARD_H_IPAD)
        else:
            panoramic_ipad = make_gradient(grad["top_color"], grad["bottom_color"], total_w_ipad, CARD_H_IPAD)

        for locale in locales:
            print(f"\nProcessing iPad {locale}...")
            process_locale_ipad(locale, panoramic_ipad, ipad_img, cfg)
    else:
        # generate_ipad_previews is false — copy any iPad screenshots straight to output
        for locale in locales:
            ss_dir = SS_DIR / locale
            ipad_shots = sorted(ss_dir.glob("iPad*.png")) if ss_dir.exists() else []
            if not ipad_shots:
                continue
            out_dir = OUTPUT_DIR / locale
            out_dir.mkdir(parents=True, exist_ok=True)
            print(f"\nCopying iPad screenshots for {locale}...")
            for src in ipad_shots:
                dst = out_dir / src.name
                shutil.copy2(src, dst)
                print(f"    ✓ {dst.relative_to(SCRIPT_DIR)}")

    print(f"\nDone. Output → {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
