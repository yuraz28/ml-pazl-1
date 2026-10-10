#!/usr/bin/env python3
"""Собирает данные для оглавления index.html в корне репозитория.

Единственный источник данных — config.yaml: номер занятия, название, дата
открытия, ссылка на презентацию (presentation) и на описание в markdown (docs).
Папки репозитория не сканируются и структура classes/ ни при чём: файлы могут
лежать сколь угодно глубоко, путь указывается от корня репозитория.

Из презентации дополнительно снимаются название (<title>) и число слайдов,
из markdown собирается страница docs/lesson-NN.html. Результат пишется в
JSON-блок внутри index.html между маркерами <!--lessons:data--> … <!--/lessons:data-->.

Скрипт детерминированный: без дат и прочего, что меняется от запуска к запуску,
поэтому повторный прогон на неизменном репозитории ничего не меняет и в CI
не даёт пустых коммитов.

Запуск:
    uv run python scripts/build_lesson_index.py            # обновить
    uv run python scripts/build_lesson_index.py --check    # только проверить (exit 1, если есть расхождения)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX = REPO_ROOT / "index.html"
CONFIG = REPO_ROOT / "config.yaml"
DOCS = REPO_ROOT / "docs"          # собранные страницы «Описание» из markdown
METOD = REPO_ROOT / "metod"        # служебная страница «/metod»: все занятия открыты

try:  # необязательная зависимость: без неё описание собирается как обычный текст
    import markdown as _markdown
except ImportError:  # pragma: no cover
    _markdown = None

OPEN_MARK = "<!--lessons:data-->"
CLOSE_MARK = "<!--/lessons:data-->"

DIR_RE = re.compile(r"^(\d+)-lessons$")  # структуру репозитория не читаем
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
SLIDE_RE = re.compile(r"<section[^>]*class=\"[^\"]*\bslide\b")
LESSON_PREFIX_RE = re.compile(r"^\s*Занятие\s*\d+\s*[.:—–-]?\s*")
SUFFIX_RE = re.compile(r"\s*[—–-]\s*Код\s*и\s*пазл\s*$", re.I)


def repo_rel_path(value) -> tuple[Path, str] | None:
    """Путь из config.yaml → (файл на диске, путь для ссылки в странице).

    Путь считается от корня репозитория и может лежать на любой глубине
    (`classes/01-lessons/01.html`, `материалы/2026/модуль-1/тема/дека.html`).
    Всё, что выводит за пределы репозитория, отбрасывается.
    """
    raw = re.sub(r"^\./+", "", str(value).strip().replace("\\", "/")).lstrip("/")
    if not raw:
        return None
    if ".." in Path(raw).parts:
        print(f"  ! путь вне репозитория: {value!r}", file=sys.stderr)
        return None
    return REPO_ROOT / raw, Path(raw).as_posix()


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


OPEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ``` / ~~~ с отступом. Python-Markdown распознаёт фенс только с первого
# столбца, поэтому блок кода внутри списка он не превращает в код — в выводе
# остаётся текст «```python». GitHub такие блоки понимает. Здесь мы собираем
# такой блок в готовый <pre><code> на том же отступе: код остаётся внутри
# пункта списка и всё равно отрисовывается.
FENCE_RE = re.compile(
    r"^(?P<indent>[ ]+)(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[A-Za-z0-9_+#.-]*)[ \t]*$"
)


def inline_fences(md_text: str) -> str:
    """Фенсы с отступом → <pre><code> (markdown их иначе не понимает)."""
    lines = md_text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = FENCE_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        indent, fence, lang = m.group("indent"), m.group("fence"), m.group("lang")
        close_re = re.compile(r"^" + re.escape(fence[0]) + "{%d,}[ \t]*$" % len(fence))
        body: list[str] = []
        j, closed = i + 1, False
        while j < n:
            line = lines[j]
            if line.startswith(indent):
                chunk = line[len(indent):]
            elif not line.strip():
                chunk = ""
            elif close_re.match(line.strip()):
                closed, j = True, j + 1
                break
            else:
                break
            if close_re.match(chunk):
                closed, j = True, j + 1
                break
            body.append(chunk)
            j += 1
        if not closed:                          # фенс не закрыт — оставляем как есть
            out.append(lines[i])
            i += 1
            continue
        cls = f' class="language-{lang}"' if lang else ""
        code = html.escape("\n".join(body))
        out.append(f"{indent}<pre><code{cls}>{code}\n</code></pre>")
        i = j
    return "\n".join(out)


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


class BuildError(Exception):
    """Собирать нечего: конфига нет или в нём нет занятий."""


def lesson_entry(item: dict) -> dict:
    """Данные одного занятия — строго по полям config.yaml.

    Структура репозитория не учитывается: `presentation` и `docs` — обычные
    пути от корня, файл может лежать на любой глубине.
    """
    n = int(item["n"])
    entry: dict = {
        "n": n,
        "title": str(item.get("title") or "").strip(),
        "slides": 0,
        "path": None,
    }

    pres = str(item.get("presentation") or "").strip()
    if pres:
        found = repo_rel_path(pres)
        if found is not None:
            deck, url = found
            if deck.is_file():
                title, slides = deck_meta(deck)
                entry.update(path=url, slides=slides, title=entry["title"] or title)
            else:
                print(f"  ! занятие {n}: презентация из config.yaml не найдена: {url}",
                      file=sys.stderr)

    opens = item.get("opens")
    if opens is not None:
        val = str(opens).strip()
        if val:
            if val != "soon" and not OPEN_RE.match(val):
                print(f"  ! занятие {n}: opens не дата и не soon: {val!r}", file=sys.stderr)
            entry["opens"] = val

    docs = str(item.get("docs") or "").strip()
    if docs:
        doc = write_doc(docs, entry)
        if doc:
            entry["doc"] = doc
    return entry


# Страница «Описание»: светлая карточка с типографикой на тёмном фоне сайта.
# Подстановка через .replace, чтобы фигурные скобки в CSS не конфликтовали.
DOC_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ — Код и пазл</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --blue:#1E7EE8; --blue-700:#1168CC; --blue-900:#0B3E7A; --night:#082A52;
    --tint:#E9F4FF; --ink:#101A26; --ink2:#38475A; --line:#E3EDF8;
    --mono:'SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace;
  }
  *{box-sizing:border-box}
  html{min-height:100%; background:radial-gradient(1300px 760px at 12% -12%,#12508F 0%,#0A3160 38%,var(--night) 62%,#061D38 100%) #061D38}
  body{margin:0; font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; color:var(--ink); -webkit-font-smoothing:antialiased}
  .wrap{max-width:860px; margin:0 auto; padding:40px 20px 72px}
  .back{display:inline-flex; align-items:center; gap:8px; margin-bottom:20px; color:#BFE1FF; text-decoration:none; font-size:14px; font-weight:600}
  .back:hover{color:#fff}
  article{background:#fff; border-radius:22px; padding:clamp(24px,4vw,52px); box-shadow:0 18px 44px rgba(3,17,34,.34)}
  article h1{font-size:clamp(26px,3.2vw,38px); line-height:1.15; letter-spacing:-.02em; margin:0 0 .6em}
  article h2{font-size:clamp(20px,2.4vw,26px); margin:1.7em 0 .5em; letter-spacing:-.01em; padding-top:.2em; border-top:1px solid var(--line)}
  article h2:first-of-type{border-top:0; padding-top:0}
  article h3{font-size:18px; margin:1.5em 0 .4em; color:var(--blue-900)}
  article p, article li{font-size:16px; line-height:1.72; color:var(--ink2)}
  article a{color:var(--blue-700)}
  article strong{color:var(--ink)}
  article code{font-family:var(--mono); font-size:.92em; background:var(--tint); color:var(--blue-900); padding:.15em .45em; border-radius:6px}
  article pre{background:#0B1E33; color:#DCEBFF; padding:16px 18px; border-radius:14px; overflow:auto}
  article pre code{background:none; color:inherit; padding:0}
  article ul, article ol{padding-left:1.3em; margin:.5em 0 1em}
  article li{margin:.28em 0}
  article blockquote{margin:1em 0; padding:.4em 1.1em; border-left:4px solid var(--blue); background:var(--tint); border-radius:0 12px 12px 0}
  article blockquote p{margin:.4em 0}
  article table{width:100%; border-collapse:collapse; margin:1em 0 1.4em; font-size:15px}
  article th, article td{border:1px solid var(--line); padding:10px 12px; text-align:left; vertical-align:top}
  article th{background:var(--tint); color:var(--blue-900); font-weight:700}
  article tr:nth-child(even) td{background:#F7FBFF}
  article img{max-width:100%; border-radius:12px}
  article hr{border:0; border-top:1px solid var(--line); margin:2em 0}
  @media (max-width:640px){ .wrap{padding:24px 14px 56px} }
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="__BACK__">&larr; Все занятия</a>
  <article>
__BODY__
  </article>
</div>
</body>
</html>
"""


def write_doc(src_rel: str, entry: dict) -> str | None:
    """Собирает страницу «Описание» из markdown-файла в docs/lesson-NN.html.

    Возвращает относительный путь к собранной странице или None, если файла нет.
    """
    src = REPO_ROOT / src_rel
    if not src.is_file():
        print(f"  ! занятие {entry['n']}: файл описания не найден: {src_rel}", file=sys.stderr)
        return None
    text = src.read_text(encoding="utf-8", errors="replace")
    if _markdown is not None:
        body = _markdown.markdown(inline_fences(text), extensions=["extra", "sane_lists"])
        # markdown оборачивает вставленный в список <pre> в <p> — убираем обёртку
        body = re.sub(r"<p>\s*(<pre>.*?</pre>)\s*</p>", r"\1", body, flags=re.S)
    else:
        print(f"  ! markdown не установлен — описание {src_rel} собрано как текст "
              f"(pip install markdown)", file=sys.stderr)
        body = "<pre>" + html.escape(text) + "</pre>"
    title = str(entry.get("title") or f"Занятие {entry['n']}").strip()
    DOCS.mkdir(parents=True, exist_ok=True)
    out = DOCS / f"lesson-{entry['n']:02d}.html"
    page = (DOC_PAGE
            .replace("__TITLE__", html.escape(title))
            .replace("__BACK__", "../index.html")
            .replace("__BODY__", body))
    out.write_text(page, encoding="utf-8")
    return f"docs/{out.name}"


def collect() -> dict:
    """Данные для оглавления. Источник один — config.yaml, папки не смотрим."""
    cfg = load_config()
    items = cfg.get("lessons")
    if not isinstance(items, list) or not items:
        raise BuildError(
            f"{CONFIG.name}: нет непустого списка lessons — собирать оглавление не из чего; "
            f"занятия описываются только в этом файле."
        )

    lessons: list[dict] = []
    at: dict[int, int] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("n") is None:
            print(f"  ! в config.yaml запись без номера занятия — пропускаю: {item!r}",
                  file=sys.stderr)
            continue
        try:
            n = int(item["n"])
        except (TypeError, ValueError):
            print(f"  ! занятие с некорректным n — пропускаю: {item.get('n')!r}", file=sys.stderr)
            continue
        if n in at:
            print(f"  ! занятие {n} в config.yaml встречается дважды — беру последнюю запись",
                  file=sys.stderr)
            lessons[at[n]] = lesson_entry(item)
        else:
            at[n] = len(lessons)
            lessons.append(lesson_entry(item))

    lessons.sort(key=lambda e: e["n"])
    course = cfg.get("course")
    if not isinstance(course, dict):
        course = {}
    return {
        "schema": 1,
        "title": str(course.get("title") or "Все занятия курса"),
        "lessons": lessons,
    }


def render_json_block(data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return f'{OPEN_MARK}\n  <script id="lessons" type="application/json">\n{body}\n  </script>\n  {CLOSE_MARK}'


def build_metod(index_text: str) -> str:
    """Страница «/metod» — то же оглавление, но все занятия открыты.

    Собирается из готового index.html, чтобы шаблон был один. Отличия:
    data-unlock-all (страница игнорирует дату opens), <base href="../"> —
    относительные ссылки classes/… и docs/… ведут в корень сайта,
    и noindex, чтобы страница не попадала в поиск.
    """
    page = index_text.replace('<html lang="ru">', '<html lang="ru" data-unlock-all>', 1)
    page = page.replace(
        '<meta charset="utf-8">',
        '<meta charset="utf-8">\n'
        '<base href="../">\n'
        '<meta name="robots" content="noindex,nofollow">',
        1,
    )
    return page.replace('<title>Материалы курса — ПАЗЛ</title>',
                        '<title>Материалы курса — ПАЗЛ — проверка</title>', 1)


def patch_index(data: dict, check_only: bool) -> int:
    stats = (f"{len(data['lessons'])} занятий, "
             f"{sum(1 for e in data['lessons'] if e.get('path'))} с презентациями, "
             f"{sum(1 for e in data['lessons'] if e.get('doc'))} с описанием")
    text = INDEX.read_text(encoding="utf-8")
    i, j = text.find(OPEN_MARK), text.find(CLOSE_MARK)
    if i == -1 or j == -1 or j < i:
        print(f"Не нашёл маркеры {OPEN_MARK} … {CLOSE_MARK} в {INDEX}", file=sys.stderr)
        return 2
    new_text = text[:i] + render_json_block(data) + text[j + len(CLOSE_MARK):]

    metod_out = METOD / "index.html"
    metod_page = build_metod(new_text)
    metod_old = metod_out.read_text(encoding="utf-8") if metod_out.is_file() else None

    fresh = []
    if new_text != text:
        fresh.append(f"{INDEX.name} нужно пересобрать")
    if metod_old != metod_page:
        fresh.append(f"{metod_out.relative_to(REPO_ROOT)} нужно пересобрать")

    if not fresh:
        print(f"OK: оглавление актуально ({stats})")
        return 0
    if check_only:
        for line in fresh:
            print(f"РАСХОЖДЕНИЕ: {line}", file=sys.stderr)
        return 1

    if new_text != text:
        INDEX.write_text(new_text, encoding="utf-8")
        print(f"Обновил {INDEX.relative_to(REPO_ROOT)}: {stats}")
    else:
        print(f"OK: {INDEX.name} уже актуален ({stats})")
    METOD.mkdir(parents=True, exist_ok=True)
    metod_out.write_text(metod_page, encoding="utf-8")
    print(f"Обновил {metod_out.relative_to(REPO_ROOT)} — страница проверки, все занятия открыты")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Собрать оглавление classes/index.html")
    ap.add_argument("--check", action="store_true", help="не писать, только проверить актуальность")
    args = ap.parse_args()
    if not INDEX.exists():
        print(f"Нет файла {INDEX}", file=sys.stderr)
        return 2
    try:
        data = collect()
    except BuildError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return patch_index(data, args.check)


if __name__ == "__main__":
    raise SystemExit(main())