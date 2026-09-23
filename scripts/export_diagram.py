"""Export the architecture HTML using local Playwright and Google Chrome.

Run `.venv/bin/python scripts/export_diagram.py` from the repository root.
No network resources or browser downloads are needed during export.
"""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "ARCHITECTURE.html"
PDF = ROOT / "CINDRELO_ARCHITECTURE.pdf"
PREVIEW = ROOT / "outputs" / "architecture-preview.png"


def main() -> int:
    if not SOURCE.is_file():
        print(f"Architecture HTML is missing: {SOURCE}", file=sys.stderr)
        return 1

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        print(
            "Export requires Python Playwright and an installed Google Chrome browser. "
            "Run this script with the project's .venv interpreter, where Playwright is installed.",
            file=sys.stderr,
        )
        return 1

    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
                page.goto(SOURCE.as_uri(), wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                page.emulate_media(media="print")
                page.pdf(
                    path=str(PDF),
                    prefer_css_page_size=True,
                    print_background=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
                page.emulate_media(media="screen")
                page.screenshot(path=str(PREVIEW), full_page=True)
            finally:
                browser.close()
    except Error as exc:
        print(
            "Diagram export failed. Google Chrome must be installed and permitted to launch "
            f"in this environment.\n{exc}",
            file=sys.stderr,
        )
        return 1

    print(f"PDF: {PDF}")
    print(f"Preview: {PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
