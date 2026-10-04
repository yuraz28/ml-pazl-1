#!/usr/bin/env python3
"""
Конвертер больших HTML-презентаций (слайды 1920x1080) в PDF.

Зависимости:
    uv run playwright install chromium

Примеры:
    uv run scripts/convert_html_to_pdf.py classes/01-lessons/01-vvedenie-v-professiyu.html
"""
import argparse
import asyncio
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print(
        "Не установлен Playwright.\n"
        "  uv run playwright install chromium",
        file=sys.stderr,
    )
    raise SystemExit(1)


PX_PER_INCH = 96.0  # CSS-пиксели в дюйме (для Chrome)


def px_to_in(px: float) -> float:
    return px / PX_PER_INCH


def make_print_css(width_px: int, height_px: int) -> str:
    """CSS, делающий «один .slide = одна страница PDF».

    Ключевое:
      * .deck  — обёртка, должна стать 'сквозной' (height:auto, overflow:visible),
                 иначе её overflow:hidden из базовых стилей обрежет всё, кроме 1-го слайда.
      * .slide — каждый слайд фиксируем ровно {W}x{H} и разрываем страницу после него.
    """
    return f"""
@media print {{
  @page {{ size: {width_px}px {height_px}px; margin: 0; }}

  html, body {{
    overflow: visible !important;
    background: #fff !important;
    margin: 0 !important;
    padding: 0 !important;
    height: auto !important;
    width: auto !important;
  }}

  /* Стадия-обёртка: снимаем fixed/inset/overflow */
  .stage {{
    position: static !important;
    inset: auto !important;
    left: auto !important; top: auto !important;
    right: auto !important; bottom: auto !important;
    overflow: visible !important;
    height: auto !important;
    width: auto !important;
  }}

  /* ★ ГЛАВНОЕ ИСПРАВЛЕНИЕ: колода не режет содержимое */
  .deck {{
    position: static !important;
    inset: auto !important;
    left: auto !important; top: auto !important;
    transform: none !important;      /* перебиваем inline-style от JS-скейлера */
    overflow: visible !important;    /* ← было overflow:hidden из базы */
    height: auto !important;         /* ← было 1080px — из-за этого 1 страница */
    width: {width_px}px !important;
    min-height: 0 !important;
    box-shadow: none !important;
    margin: 0 !important;
  }}

  /* Каждый слайд — самостоятельная страница */
  .slide {{
    position: relative !important;
    inset: auto !important;
    display: flex !important;
    width: {width_px}px !important;
    height: {height_px}px !important;
    opacity: 1 !important;           /* иначе видны только .active */
    pointer-events: auto !important;
    overflow: hidden !important;     /* внутренний клип по границам слайда — ок */
    margin: 0 !important;
    box-shadow: none !important;
    break-after: page;
    page-break-after: always;
    break-inside: avoid;
    page-break-inside: avoid;
  }}
  .slide:last-of-type {{ break-after: auto; page-break-after: auto; }}

  /* Анимации появления не нужны — рисуем финальное состояние */
  .slide .r, .pop, .run-code .ln, .run-code .ln.on {{
    animation: none !important;
    opacity: 1 !important;
    transform: none !important;
  }}

  .hud {{ display: none !important; }}
}}
"""


async def convert_one(
    browser,
    src: Path,
    dst: Path,
    width: int,
    height: int,
    timeout_ms: int,
    wait_extra_s: float,
    inject_print_css: bool,
) -> None:
    page = await browser.new_page(viewport={"width": width, "height": height})
    try:
        url = src.resolve().as_uri()
        await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

        # Ждём загрузку шрифтов (Google Fonts и т.п.)
        try:
            await page.evaluate("document.fonts && document.fonts.ready")
        except Exception:
            pass

        if wait_extra_s > 0:
            await page.wait_for_timeout(int(wait_extra_s * 1000))

        if inject_print_css:
            await page.add_style_tag(content=make_print_css(width, height))

        # Заставляем браузер применить @media print
        await page.emulate_media(media="print")

        await page.pdf(
            path=str(dst),
            width=f"{px_to_in(width):.4f}in",
            height=f"{px_to_in(height):.4f}in",
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            print_background=True,
            prefer_css_page_size=False,
        )
    finally:
        await page.close()


async def main_async(args: argparse.Namespace) -> int:
    # Собираем список файлов
    inputs: list[Path] = []
    for pattern in args.inputs:
        p = Path(pattern)
        if p.is_dir():
            inputs.extend(sorted(p.glob("*.html")))
        elif p.exists():
            inputs.append(p)
        else:
            print(f"Не найдено: {pattern}", file=sys.stderr)

    if not inputs:
        print("Нет входных файлов.", file=sys.stderr)
        return 2

    if args.outdir:
        Path(args.outdir).mkdir(parents=True, exist_ok=True)

    failures = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for src in inputs:
                if args.output and len(inputs) == 1:
                    dst = Path(args.output)
                elif args.outdir:
                    dst = Path(args.outdir) / (src.stem + ".pdf")
                else:
                    dst = src.with_suffix(".pdf")

                dst.parent.mkdir(parents=True, exist_ok=True)
                print(f"→ {src}  ⇒  {dst}")
                try:
                    await convert_one(
                        browser, src, dst,
                        width=args.width,
                        height=args.height,
                        timeout_ms=int(args.timeout * 1000),
                        wait_extra_s=args.wait,
                        inject_print_css=not args.no_print_css,
                    )
                except Exception as e:
                    failures += 1
                    print(f"  ошибка: {e}", file=sys.stderr)
        finally:
            await browser.close()

    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Конвертер HTML-презентаций в PDF (headless Chromium через Playwright).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("inputs", nargs="+", help="HTML-файлы или папки с ними")
    p.add_argument("-o", "--output", help="имя PDF (только для одного файла)")
    p.add_argument("--outdir", help="папка для результирующих PDF")
    p.add_argument("--width",  type=int, default=1920, help="ширина слайда в CSS-px")
    p.add_argument("--height", type=int, default=1080, help="высота слайда в CSS-px")
    p.add_argument("--wait",   type=float, default=0.6,
                   help="доп. ожидание после загрузки, сек (для шрифтов/картинок)")
    p.add_argument("--timeout", type=float, default=60.0,
                   help="таймаут навигации, сек")
    p.add_argument("--no-print-css", action="store_true",
                   help="не инжектить встроенный @media print")
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())