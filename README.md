# Aether Automation

**On-device accessibility automation with embedded CPython, OpenCV vision, and MediaProjection screen capture.**

Aether is a complete Android application that runs computer vision macros powered by Python, NumPy, and OpenCV—entirely on your device, with zero cloud connectivity.

## Features

- **Embedded CPython 3.10** via Chaquopy
- **Screen Capture** via MediaProjection API (raw RGBA frames, no JPEG round-trip)
- **Vision Pipeline** with OpenCV + NumPy
- **Accessibility Injection** for automated taps, swipes, and UI interaction
- **Floating HUD** for macro control and live console output
- **Template Capture** tool for cropping and saving screen regions
- **100% Device-Local** — no cloud, no telemetry

## Quick Start

### Building Locally

```bash
# Prerequisites: Python 3.9+, JDK 17, ~10 GB free disk
git clone https://github.com/jellysinclair1324-gif/AetherAutomation.git
cd AetherAutomation

# Generate project and build APK
python3 build_aether.py --build
```

### GitHub Actions (Automated)

Every push to `main` triggers an automatic build. Download the APK from:
- **Actions** tab → Latest workflow run → **aether-debug-apk** artifact

### Installation on Device

1. Transfer APK via USB or download from GitHub Actions
2. Install: `adb install app/build/outputs/apk/debug/app-debug.apk`
3. Grant required permissions:
   - Display over other apps (Settings > Apps > special permissions)
   - Notifications (Settings > Apps > Permissions)
   - Accessibility (Settings > Accessibility > Installed apps > Aether Injector)
4. Open app and tap **Start engine**
5. Tap the floating bubble to open the controller

## Architecture

```
Android App (Kotlin)
  ├─ MainActivity: Permission wizard
  ├─ AetherEngineService: Foreground service (mediaProjection type)
  │  ├─ ScreenCapturer: VirtualDisplay → ImageReader → Frame (RGBA_8888)
  │  ├─ OverlayHud: Floating controller + console
  │  └─ AccessibilityAutomationService: Gesture injection
  │
  ├─ Chaquopy Runtime (CPython 3.10)
  │  ├─ automation_runner.py: Entry point
  │  ├─ vision_pipeline.py: OpenCV utilities
  │  └─ Your macros: *.py
  │
  └─ AetherBridge: Java↔Python interface
     ├─ captureFrame() → Frame
     ├─ tap(x, y, holdMs) → bool
     ├─ swipe(x1, y1, x2, y2, durationMs) → bool
     └─ findText(text) → [left, top, right, bottom] or null
```

## Writing Macros

Macros are Python modules. Example:

```python
# automation_runner.py
import aether
import cv2
import numpy as np

def run(name: str):
    """Entry point called by the HUD's Run button."""
    if name == "example":
        return example_macro()
    return f"Unknown macro: {name}"

def list_macros():
    """Return macro names for the HUD dropdown."""
    return ["example"]

def example_macro():
    # Capture the screen
    frame = aether.captureFrame(5000)  # 5s timeout
    if not frame:
        return "No frame"
    
    # Convert to OpenCV format
    img = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
    
    # Run vision: find template
    template_path = aether.templatesDir() + "/button.png"
    template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    
    if max_val > 0.8:
        x, y = max_loc
        aether.tap(x, y, 100)  # Tap for 100ms
        return f"Tapped at {x},{y}"
    
    return "Template not found"
```

## Troubleshooting

### "Accessibility injector is not enabled"
The app can still capture and crop templates, but input injection requires the AccessibilityService. Enable it in Settings > Accessibility.

### "No frame available yet - move the screen once"
MediaProjection only emits frames when content changes. Tap a button or scroll.

### Build fails: "Python version mismatch"
The script auto-detects or installs a buildPython matching Chaquopy's runtime. Re-run:
```bash
python3 build_aether.py --build
```

## License

MIT. See LICENSE file.

## Contributing

Issues, PRs, and macro examples welcome!
