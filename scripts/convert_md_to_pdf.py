#!/usr/bin/env python3
"""
Конвертирует Markdown-файл в PDF с помощью библиотеки markdown-pdf.

Примеры:
    uv run python scripts/convert_md_to_pdf.py classes/01-lessons/text.md

"""

import re
import sys
from pathlib import Path
from markdown_pdf import MarkdownPdf, Section


TABLE_CSS = """
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}
table th, table td {
    border: 1px solid #444;
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
}
table th {
    font-weight: bold;
    border-bottom: 2px solid #000;
}
"""


def clean_markdown(text: str) -> str:
    """Убирает строки, из-за которых рендерер создаёт пустые таблицы."""
    lines = text.splitlines()
    cleaned = []

    separator_re = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
    only_pipes_re = re.compile(r"^\s*\|[\s|]*\|\s*$")

    for i, line in enumerate(lines):
        # строка только из пайпов и пробелов ("| | |")
        if only_pipes_re.match(line):
            continue
        # строка-разделитель таблицы, у которой сверху нет заголовка
        if separator_re.match(line):
            prev = cleaned[-1] if cleaned else ""
            if "|" not in prev:
                continue
        cleaned.append(line)

    return "\n".join(cleaned)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Использование: {sys.argv[0]} <путь_к_md_файлу>", file=sys.stderr)
        return 1

    input_path = Path(sys.argv[1]).expanduser().resolve()
    if not input_path.is_file():
        print(f"Файл не найден: {input_path}", file=sys.stderr)
        return 1
    if input_path.suffix.lower() != ".md":
        print(f"Ожидается .md, получено: {input_path.name}", file=sys.stderr)
        return 1

    output_path = input_path.with_suffix(".pdf")

    with open(input_path, "r", encoding="utf-8") as f:
        markdown_text = f.read()

    markdown_text = clean_markdown(markdown_text)

    pdf = MarkdownPdf(toc_level=2)
    pdf.add_section(Section(markdown_text), user_css=TABLE_CSS)
    pdf.save(str(output_path))

    print(f"Готово: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())