#!/usr/bin/env python3
"""Собирает данные для оглавления index.html в корне репозитория.

Сканирует classes/<NN>-lessons/, вытаскивает из презентаций название
(<title>) и число слайдов и переписывает JSON-блок внутри
index.html между маркерами <!--lessons:data--> … <!--/lessons:data-->.

Скрипт детерминированный: без дат и прочего, что меняется от запуска к запуску,
поэтому повторный прогон на неизменном репозитории ничего не меняет и в CI
не даёт пустых коммитов.

Запуск:
    python3 scripts/build_lesson_index.py            # обновить
    python3 scripts/build_lesson_index.py --check    # только проверить (exit 1, если есть расхождения)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSES = REPO_ROOT / "classes"
INDEX = REPO_ROOT / "index.html"
CONFIG = REPO_ROOT / "config.yaml"

OPEN_MARK = "<!--lessons:data-->"
CLOSE_MARK = "<!--/lessons:data-->"

DIR_RE = re.compile(r"^(\d+)-lessons$")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
SLIDE_RE = re.compile(r"<section[^>]*class=\"[^\"]*\bslide\b")
LESSON_PREFIX_RE = re.compile(r"^\s*Занятие\s*\d+\s*[.:—–-]?\s*")
SUFFIX_RE = re.compile(r"\s*[—–-]\s*Код\s*и\s*пазл\s*$", re.I)


def git_origin() -> str:
    """owner/repo из origin — чтобы страница знала, где искать свежие презентации."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return ""
    if not url:
        return ""
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def deck_meta(path: Path) -> tuple[str, int]:
    """Название занятия и число слайдов из файла презентации."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = TITLE_RE.search(text)
    title = ""
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        title = SUFFIX_RE.sub("", title)
        title = LESSON_PREFIX_RE.sub("", title)
    slides = len(SLIDE_RE.findall(text))
    return title, slides


def pick_deck(lesson_dir: Path, n: int) -> Path | None:
    decks = sorted(p for p in lesson_dir.glob("*.html") if p.is_file())
    if not decks:
        return None
    preferred = [p for p in decks if p.name.startswith(f"{n}-")]
    chosen = preferred[0] if preferred else decks[0]
    if len(decks) > 1:
        print(f"  ! в {lesson_dir.name} несколько .html, беру {chosen.name}", file=sys.stderr)
    return chosen


OPEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_config() -> dict:
    """Общий config.yaml (course + lessons). Нет файла/нет PyYAML — работаем без него."""
    if not CONFIG.exists():
        return {}
    try:
        import yaml
    except ImportError:
        print(f"  ! {CONFIG.name} есть, но PyYAML не установлен — пропускаю (pip install pyyaml)",
              file=sys.stderr)
        return {}
    try:
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — конфиг пишут руками, ошибка не должна ронять сборку
        print(f"  ! не разобрал {CONFIG.name}: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def config_by_lesson(cfg: dict) -> dict:
    """Список lessons из конфига → {номер: запись}."""
    by_n = {}
    items = cfg.get("lessons")
    if not isinstance(items, list):
        return by_n
    for item in items:
        if not isinstance(item, dict) or item.get("n") is None:
            continue
        try:
            by_n[int(item["n"])] = item
        except (TypeError, ValueError):
            print(f"  ! пропускаю урок с некорректным n: {item.get('n')!r}", file=sys.stderr)
    return by_n


def apply_overrides(entry: dict, c: dict) -> None:
    """Наложить поля урока из config.yaml поверх данных, снятых с деки."""
    if not isinstance(c, dict) or not c:
        return

    pres = c.get("presentation")
    if pres:
        pres = str(pres).strip()
        path = REPO_ROOT / pres
        if path.is_file():
            title, slides = deck_meta(path)
            entry.update(
                file=path.name,
                dir=path.parent.name,
                path=pres,
                slides=slides,
                title=title,
            )
        else:
            print(f"  ! занятие {entry['n']}: презентация из config.yaml не найдена: {pres}",
                  file=sys.stderr)

    if c.get("title"):
        entry["title"] = str(c["title"]).strip()

    opens = c.get("opens")
    if opens is not None:
        val = str(opens).strip()
        if val:
            if val != "soon" and not OPEN_RE.match(val):
                print(f"  ! занятие {entry['n']}: opens не дата и не soon: {val!r}", file=sys.stderr)
            entry["opens"] = val


def collect() -> dict:
    cfg = load_config()
    by_n = config_by_lesson(cfg)

    lessons = []
    for lesson_dir in sorted(CLASSES.iterdir(), key=lambda p: p.name):
        if not lesson_dir.is_dir():
            continue
        m = DIR_RE.match(lesson_dir.name)
        if not m:
            continue
        n = int(m.group(1))
        deck = pick_deck(lesson_dir, n)
        entry = {"n": n, "dir": lesson_dir.name, "file": None, "title": "", "slides": 0}
        if deck is not None:
            title, slides = deck_meta(deck)
            entry.update(
                file=deck.name,
                title=title,
                slides=slides,
                path=f"classes/{lesson_dir.name}/{deck.name}",
            )
        lessons.append(entry)

    # занятия, объявленные только в config.yaml (папки нет / ещё не завели)
    known = {e["n"] for e in lessons}
    for n in sorted(by_n):
        if n not in known:
            lessons.append({"n": n, "dir": f"{n:02d}-lessons", "file": None, "title": "", "slides": 0})

    lessons.sort(key=lambda e: e["n"])
    for entry in lessons:
        apply_overrides(entry, by_n.get(entry["n"], {}))

    course = cfg.get("course")
    if not isinstance(course, dict):
        course = {}
    return {
        "schema": 1,
        "branch": str(course.get("branch") or "main"),
        "gh": git_origin(),
        "title": str(course.get("title") or "Все занятия курса"),
        "lessons": lessons,
    }


def render_json_block(data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return f'{OPEN_MARK}\n  <script id="lessons" type="application/json">\n{body}\n  </script>\n  {CLOSE_MARK}'


def patch_index(data: dict, check_only: bool) -> int:
    text = INDEX.read_text(encoding="utf-8")
    i, j = text.find(OPEN_MARK), text.find(CLOSE_MARK)
    if i == -1 or j == -1 or j < i:
        print(f"Не нашёл маркеры {OPEN_MARK} … {CLOSE_MARK} в {INDEX}", file=sys.stderr)
        return 2
    block_start = i
    block_end = j + len(CLOSE_MARK)
    new_text = text[:block_start] + render_json_block(data) + text[block_end:]
    if new_text == text:
        print(f"OK: {INDEX.name} уже актуален ({len(data['lessons'])} занятий, "
              f"{sum(1 for e in data['lessons'] if e['file'])} с презентациями)")
        return 0
    if check_only:
        print(f"РАСХОЖДЕНИЕ: {INDEX.name} нужно пересобрать", file=sys.stderr)
        return 1
    INDEX.write_text(new_text, encoding="utf-8")
    print(f"Обновил {INDEX.relative_to(REPO_ROOT)}: {len(data['lessons'])} занятий, "
          f"{sum(1 for e in data['lessons'] if e['file'])} с презентациями")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Собрать оглавление classes/index.html")
    ap.add_argument("--check", action="store_true", help="не писать, только проверить актуальность")
    args = ap.parse_args()
    if not INDEX.exists():
        print(f"Нет файла {INDEX}", file=sys.stderr)
        return 2
    return patch_index(collect(), args.check)


if __name__ == "__main__":
    raise SystemExit(main())