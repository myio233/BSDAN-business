from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def main_async(html_path: Path, output_path: Path, clip_height: int | None) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 794, "height": 1200}, device_scale_factor=2)
        await page.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith(("file://", "data:", "about:blank"))
            else route.abort(),
        )
        await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        sheet = page.locator(".sheet")
        capture_root = page.locator(".capture-root")
        if clip_height:
            box = await sheet.bounding_box()
            if box is None:
                raise RuntimeError("Unable to locate .sheet bounding box for screenshot.")
            await page.screenshot(
                path=str(output_path),
                clip={
                    "x": box["x"],
                    "y": box["y"],
                    "width": box["width"],
                    "height": clip_height,
                },
            )
        else:
            await page.evaluate(
                """
                () => {
                  const root = document.querySelector('.capture-root');
                  const marker = document.querySelector('.report-end-marker');
                  if (!root || !marker) return;
                  const rootRect = root.getBoundingClientRect();
                  const markerRect = marker.getBoundingClientRect();
                  const needed = Math.ceil(markerRect.bottom - rootRect.top + 36);
                  root.style.height = `${needed}px`;
                  root.style.minHeight = `${needed}px`;
                }
                """
            )
            root_box = await capture_root.bounding_box()
            if root_box is None:
                await sheet.screenshot(path=str(output_path))
            else:
                await capture_root.screenshot(path=str(output_path))
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--clip-height", type=int)
    args = parser.parse_args()
    asyncio.run(main_async(Path(args.html), Path(args.output), args.clip_height))


if __name__ == "__main__":
    main()
