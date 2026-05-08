# App Store Preview Generator

I got tired of creating App Store screenshots one by one. Every time the UI changed, I had to redo them for every screen, every language. So I wrote a script that does it for me.

The idea is simple: take a background image, split it across N screens, put a device-framed screenshot in each one, add a title on top. Done.

I use Fastlane to run UITests and capture screenshots across all my localizations (iPhone + iPad). Then this script processes them into App Store-ready cards. Then Fastlane uploads them. The whole thing runs with one command.

![Example output](output/preview_sheet_en-US.png)

## What It Does

Takes your Fastlane screenshots and frames them into App Store preview cards:

- Composites screenshots into an iPhone device frame with Dynamic Island
- Adds configurable title and subtitle text above the device
- Applies a panoramic background image (or gradient fallback) across all cards
- Supports multi-line titles, per-screenshot font/color overrides, and text shadows
- Generates a preview sheet showing all cards side-by-side
- Optional iPad preview generation with or without device frame

## Quick Start

```bash
# 1. Install the dependency
pip install Pillow

# 2. Add your screenshots
#    Place Fastlane screenshots in screenshots/{locale}/
#    e.g. screenshots/en-US/iPhone 16 Pro Max-01_Dashboard.png

# 3. Configure your titles
#    Edit metadata/en-US.json (see below)

# 4. Generate
python3 generate_previews.py --all
```

Output lands in `output/{locale}/`.

## Directory Structure

```
app-store-preview-generator/
    generate_previews.py        # The script
    config.json                 # Global layout, typography, colors
    phone.png                   # iPhone frame (800x1630, RGBA)
    notch_camera.png            # Dynamic Island overlay (RGBA)
    banner.jpg                  # Background image (optional)
    metadata/
        en-US.json              # Titles per screenshot per locale
        de-DE.json
        ...
    screenshots/                # Raw Fastlane screenshots (input)
        en-US/
            iPhone 16 Pro Max-01_Dashboard.png
            ...
    output/                     # Generated previews (output)
        en-US/
            01_Dashboard.png
            ...
        preview_sheet_en-US.png
```

## Usage

```bash
# Single locale
python3 generate_previews.py --locale en-US

# All locales (auto-discovered from metadata/)
python3 generate_previews.py --all

# Override screen region (x,y,w,h in frame coordinates)
python3 generate_previews.py --all --screen-region 32,12,736,1600
```

## Configuration

### config.json

Controls the global layout, typography, and visual style.

```jsonc
{
  // Background image filename (falls back to gradient if missing)
  "banner_file": "banner.jpg",

  "layout": {
    "phone_scale": 0.88,           // Phone width as fraction of card width
    "phone_bottom_pad": -200,      // Negative = phone overlaps card bottom
    "text_top_pad": 140,           // Top of card to title text
    "title_subtitle_gap": 28,      // Gap between title and subtitle
    "screen_region": [30, 30, 740, 1570],  // Screenshot region in phone.png coords [x,y,w,h]
    "screen_corner_radius_ratio": 0.15     // Corner radius as fraction of screen width
  },

  // Title typography
  "title_font": { "family": "Avenir Next", "weight": "Bold", "size": 136 },
  "title_color": "#000000",
  "title_line_extra": 12,          // Extra line spacing for multi-line titles

  // Subtitle typography
  "show_subtitle": false,
  "subtitle_font": { "family": "Avenir Next", "weight": "Medium", "size": 68 },
  "subtitle_color": "#3C3C3E",

  // Text shadow
  "text_shadow": false,
  "text_shadow_color": "#FFFFFF",
  "text_shadow_blur": 60,

  // Background overlay (mutes the background for text readability)
  "bg_overlay_color": "#F9DAB9",
  "bg_overlay_opacity": 75,        // 0-100

  // Gradient fallback (used when no banner image is found)
  "gradient_fallback": { "top_color": "#FF5C00", "bottom_color": "#CC3300" },

  // iPad support
  "generate_ipad_previews": false,
  "ipad_layout": {
    "text_top_pad": 220,
    "title_subtitle_gap": 40,
    "screenshot_top_pad": 460,
    "screenshot_side_pad": 40,
    "screenshot_bottom_pad": 80,
    "screen_corner_radius": 50
  }
}
```

### metadata/{locale}.json

Defines titles and subtitles for each screenshot, per locale.

```json
{
  "screenshots": [
    {
      "file": "01_Dashboard",
      "title": "Create a\ndigital menu\nfor your restaurant",
      "subtitle": ""
    },
    {
      "file": "02_Templates",
      "title": "Choose from\nbeautiful\ntemplates",
      "subtitle": "Pick one that fits your brand"
    }
  ]
}
```

Use `\n` in titles for multi-line text.

#### Per-screenshot overrides

Any screenshot entry can override the global font/color:

```json
{
  "file": "03_Special",
  "title": "Something bold",
  "title_font_family": "Helvetica",
  "title_font_weight": "Bold",
  "title_font_size": 100,
  "title_color": "#FF0000"
}
```

## Assets

### phone.png

An 800x1630 RGBA PNG of an iPhone silhouette (black body, transparent background). The script composites the screenshot into the screen region defined in `config.json`.

### notch_camera.png (optional)

Same dimensions as phone.png. Contains the Dynamic Island overlay. The script auto-detects the notch bounds from the alpha channel and fills it with a black pill.

### banner.jpg (optional)

A wide background image. The script scales it to span all cards panoramically, so each card gets a different slice. If no banner is found, it falls back to the gradient defined in config.

## Adding a New Locale

1. Create `metadata/{locale}.json` with your translated titles
2. Add Fastlane screenshots to `screenshots/{locale}/`
3. Run `python3 generate_previews.py --locale {locale}`

Locales are auto-discovered from the `metadata/` directory.

## Fonts

The script has built-in support for **Avenir Next** (macOS system font) with all weight variants. For other fonts, it searches:

- `/System/Library/Fonts/{family}.ttf`
- `/Library/Fonts/{family} {weight}.ttf`
- `/Library/Fonts/{family}-{weight}.ttf`

Falls back to Pillow's default font if nothing is found.

## Integrating with Fastlane

This tool fits into a fully automated App Store screenshot pipeline. Here's how I use it with my iOS app:

### The Pipeline

```
fastlane screenshots          # 1. Capture raw screenshots via UITests
       ↓
python3 generate_previews.py  # 2. Frame them into App Store cards
       ↓
fastlane upload_previews      # 3. Upload to App Store Connect
```

Or run all three in one shot with a single lane:

```ruby
lane :screenshots_and_upload do
  screenshots       # capture
  previews          # frame
  upload_previews   # upload
end
```

### Step 1: Capture Screenshots with UITests

Fastlane's `snapshot` drives Xcode UITests on a simulator. Each test navigates to a screen and calls `snapshot("01_Dashboard")` to save a PNG. Run across multiple locales to get localized screenshots:

```ruby
lane :screenshots do
  capture_screenshots(
    scheme: "YourApp",
    devices: ["iPhone 16 Pro Max"],
    languages: ["en-US", "de-DE", "es-ES", "tr"],
    output_directory: "./screenshots"
  )
end
```

This produces:

```
screenshots/
  en-US/
    iPhone 16 Pro Max-01_Dashboard.png
    iPhone 16 Pro Max-02_Templates.png
    ...
  de-DE/
    ...
```

### Step 2: Generate Preview Cards

Point the script at the screenshots directory. It reads titles from `metadata/{locale}.json` and composites each screenshot into a framed card:

```ruby
lane :previews do
  sh("python3 path/to/generate_previews.py --all")
end
```

### Step 3: Upload to App Store Connect

Use Fastlane's `deliver` to push the framed previews to App Store Connect:

```ruby
lane :upload_previews do
  deliver(
    api_key: api_key,
    skip_binary_upload: true,
    skip_metadata: true,
    screenshots_path: "./output",
    overwrite_screenshots: true,
    force: true
  )
end
```

### Why This Exists

If you support multiple languages and devices, the number of screenshots adds up fast. 8 screens x 4 languages x 2 device types = 64 images. Doing that by hand every release is not realistic.

This script handles the iPhone framing. iPad screenshots are currently copied as-is (iPad framing may come later). Combined with Fastlane, the whole pipeline runs unattended with one command.

## Requirements

- Python 3.10+
- [Pillow](https://python-pillow.org/) (`pip install Pillow`)

## License

MIT
