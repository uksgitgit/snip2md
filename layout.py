"""Geometry-only layout for OCR boxes → GitHub-flavored Markdown.

No model. Reading order follows recursive XY-cut (Ha/Haralick 1995;
Unstructured/Sanster): shrink boxes so near-overlaps separate, cut the
page on vertical gutters first (dashboards are columns), then cut each
column on horizontal gaps, then cut a band on remaining vertical gaps
to recover card grids as tables.

XY-Cut++ (arXiv:2504.10258) phase 1 is the header band: top chrome is
emitted first and never becomes a column H1.
"""

from __future__ import annotations

import re
from itertools import zip_longest

# y, x, width, height, text
Word = tuple[float, float, float, float, str]


def words_to_markdown(words: list[Word], width: int, height: int) -> str:
    if not words:
        return ""
    if width <= 0:
        width = int(max(item[1] + item[2] for item in words)) + 1
    if height <= 0:
        height = int(max(item[0] + item[3] for item in words)) + 1
    header, body = _peel_header(words, width, height)
    parts: list[str] = []
    chrome = _format_chrome(header)
    if chrome:
        parts.append(chrome)
    columns = _split_groups(
        body,
        axis="x",
        min_gap=max(36, int(width * 0.04)),
        shrink=0.9,
    )
    if not columns:
        columns = [body]
    paired = _format_paired_columns(columns, width)
    if paired:
        parts.append(paired)
    else:
        for column in columns:
            chunk = _format_column(column, page_width=width)
            if chunk:
                parts.append(chunk)
    return "\n\n".join(parts).strip()


def _peel_header(
    words: list[Word], width: int, height: int
) -> tuple[list[Word], list[Word]]:
    header: list[Word] = []
    body: list[Word] = []
    for word in words:
        y, x, w, _h, _text = word
        if y + _h <= min(40.0, height * 0.08) and x > width * 0.55:
            header.append(word)
        else:
            body.append(word)
    return header, body or words


def _shrunk(word: Word, factor: float) -> tuple[int, int, int, int]:
    y, x, w, h, _text = word
    cx = x + w / 2.0
    cy = y + h / 2.0
    nw = max(1.0, w * factor)
    nh = max(1.0, h * factor)
    x0 = int(cx - nw / 2.0)
    y0 = int(cy - nh / 2.0)
    x1 = int(cx + nw / 2.0)
    y1 = int(cy + nh / 2.0)
    if x1 <= x0:
        x1 = x0 + 1
    if y1 <= y0:
        y1 = y0 + 1
    return x0, y0, x1, y1


def _split_groups(
    words: list[Word],
    *,
    axis: str,
    min_gap: int,
    shrink: float,
) -> list[list[Word]]:
    if len(words) <= 1:
        return [words] if words else []
    boxes = [_shrunk(word, shrink) for word in words]
    if axis == "x":
        start_i, end_i = 0, 2
        center = lambda word: word[1] + word[2] / 2.0
    else:
        start_i, end_i = 1, 3
        center = lambda word: word[0] + word[3] / 2.0
    lo = min(box[start_i] for box in boxes)
    hi = max(box[end_i] for box in boxes)
    if hi - lo < min_gap * 2:
        return [words]
    occupancy = [0] * (hi - lo + 1)
    for box in boxes:
        for pixel in range(max(lo, box[start_i]), min(hi, box[end_i])):
            occupancy[pixel - lo] += 1
    cuts: list[float] = []
    index = 0
    while index < len(occupancy):
        if occupancy[index] == 0:
            other = index
            while other < len(occupancy) and occupancy[other] == 0:
                other += 1
            if other - index >= min_gap and index > 0 and other < len(occupancy):
                cuts.append(lo + (index + other) / 2.0)
            index = other
        else:
            index += 1
    if not cuts:
        return [words]
    groups: list[list[Word]] = [[] for _ in range(len(cuts) + 1)]
    for word in words:
        slot = 0
        value = center(word)
        for cut in cuts:
            if value < cut:
                break
            slot += 1
        groups[slot].append(word)
    return [group for group in groups if group]


def _median_height(words: list[Word]) -> float:
    heights = sorted(word[3] for word in words)
    return heights[len(heights) // 2] if heights else 12.0


def _column_blocks(words: list[Word]) -> list[tuple[str, object]]:
    """Return ('lines', words) or ('table', rows) in top-to-bottom order."""
    if not words:
        return []
    median_h = _median_height(words)
    bands = _split_groups(
        words,
        axis="y",
        min_gap=max(10, int(median_h * 0.7)),
        shrink=0.85,
    )
    blocks: list[tuple[str, object]] = []
    pending_rows: list[list[str]] = []

    def flush_table() -> None:
        nonlocal pending_rows
        if pending_rows:
            blocks.append(("table", pending_rows))
            pending_rows = []

    for band in bands:
        cell_gap = max(8, int(median_h * 0.45))
        raw_cells = _split_groups(band, axis="x", min_gap=cell_gap, shrink=0.85)
        cells: list[list[Word]] = []
        for cell in raw_cells:
            kept = [item for item in cell if not _is_noise_token(item[4])]
            if kept:
                cells.append(kept)
        both_real = len(cells) == 2 and all(
            any(len(item[4].strip()) >= 4 for item in cell) for cell in cells
        )
        use_table = 3 <= len(cells) <= 4 or both_real
        if use_table:
            columns_text: list[list[str]] = []
            for cell in cells:
                ordered = _merge_paragraph(
                    sorted(cell, key=lambda item: (item[0], item[1]))
                )
                columns_text.append([item[4] for item in ordered])
            for row in zip_longest(*columns_text, fillvalue=""):
                pending_rows.append(list(row))
        else:
            flush_table()
            blocks.append(("lines", _merge_paragraph(band)))
    flush_table()
    return blocks


def _merge_paragraph(words: list[Word]) -> list[Word]:
    """Join wrapped lines that share a left edge (e.g. '…en af' + 'dem?')."""
    ordered = sorted(words, key=lambda item: (item[0], item[1]))
    if len(ordered) < 2:
        return ordered
    merged: list[Word] = [ordered[0]]
    for word in ordered[1:]:
        prev = merged[-1]
        py, px, pw, ph, ptext = prev
        y, x, w, h, text = word
        same_left = abs(x - px) <= 10
        close_y = 0 <= (y - (py + ph)) <= max(ph, h) * 1.35
        if _is_url(text):
            merged.append(word)
            continue
        continuation = len(text) <= 24 and (
            not text[:1].isupper() or text.endswith("?")
        )
        first_is_prose = len(ptext) >= 28
        if same_left and close_y and continuation and first_is_prose:
            merged[-1] = (py, px, max(pw, x + w - px), y + h - py, f"{ptext} {text}")
        else:
            merged.append(word)
    return merged


def _is_url(line: str) -> bool:
    lower = line.lower()
    return lower.startswith("http://") or lower.startswith("https://")


def _is_email(line: str) -> bool:
    return "@" in line and "." in line.split("@")[-1]


def _clean_ocr_line(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^[0-9]{1,2}\s+(?=[A-Za-zÆØÅæøå])", "", text)
    text = re.sub(r"^[\(\[\{]?\s*[→←\-–—•·×x]\s*", "", text)
    return text.strip("()[]{} \t")


def _is_logout(line: str) -> bool:
    compact = line.lower().replace(" ", "")
    return compact in {"logud", "logout", "signout", "logoff"}


def _is_app_title(line: str) -> bool:
    if _is_url(line) or _is_email(line) or _is_logout(line) or _is_all_caps_heading(line):
        return False
    if line.endswith((".", "?", "!")) or any(char.isdigit() for char in line):
        return False
    words = line.split()
    return 1 <= len(words) <= 5 and line[:1].isupper()


def _is_identity_line(line: str) -> bool:
    if _is_email(line):
        return True
    if _is_all_caps_heading(line) or _is_logout(line) or _is_url(line):
        return False
    words = line.split()
    if not (2 <= len(words) <= 4):
        return False
    return all(
        part[:1].isupper() and (len(part) == 1 or not part[1:].isupper())
        for part in words
        if part.isalpha()
    )


def _is_nav_item(line: str) -> bool:
    if (
        _is_email(line)
        or _is_url(line)
        or _is_logout(line)
        or _is_all_caps_heading(line)
        or _is_identity_line(line)
    ):
        return False
    if len(line) > 24 or any(char.isdigit() for char in line):
        return False
    return 1 <= len(line.split()) <= 3


def _looks_like_app_chrome(lines: list[str], *, span: float) -> bool:
    if not (3 <= len(lines) <= 16):
        return False
    mean = sum(len(line) for line in lines) / len(lines)
    if mean > 26 or any(len(line) > 42 for line in lines):
        return False
    score = 0
    if any(_is_logout(line) or _is_email(line) for line in lines):
        score += 2
    if lines and _is_app_title(lines[0]):
        score += 1
    if any(_is_all_caps_heading(line) for line in lines):
        score += 1
    if sum(1 for line in lines if _is_nav_item(line)) >= 2:
        score += 1
    return score >= 2 and (span < 280 or mean <= 20)


def _format_app_chrome(lines: list[str]) -> str:
    parts: list[str] = []
    index = 0
    if lines and _is_app_title(lines[0]):
        parts.append(f"# {lines[0]}")
        index = 1
    if index < len(lines) and _is_all_caps_heading(lines[index]):
        parts.append(lines[index])
        index += 1
    nav: list[str] = []
    while index < len(lines) and _is_nav_item(lines[index]):
        nav.append(lines[index])
        index += 1
    if nav:
        parts.append("\n".join(f"- {line}" for line in nav))
    identity: list[str] = []
    while index < len(lines) and _is_identity_line(lines[index]):
        identity.append(lines[index])
        index += 1
    if identity:
        parts.append("\n".join(identity))
    rest = lines[index:]
    if rest:
        parts.append(
            "\n".join(f"- {line}" if _is_logout(line) or _is_nav_item(line) else line for line in rest)
        )
    return "\n\n".join(part for part in parts if part)


def _is_all_caps_heading(line: str) -> bool:
    letters = "".join(char for char in line if char.isalpha())
    if len(letters) < 4 or len(line) > 42:
        return False
    return letters.isupper()


_STATUS_HINTS = (
    "skrevet",
    "indsendt",
    "tjekker",
    "skriver",
    "bevilling",
    "pending",
    "in progress",
)


def _column_span(words: list[Word]) -> float:
    if not words:
        return 0.0
    left = min(item[1] for item in words)
    right = max(item[1] + item[2] for item in words)
    return right - left


def _looks_like_nav(lines: list[str], *, span: float = 9999) -> bool:
    if len(lines) < 5:
        return False
    if any(_is_url(line) or _is_email(line) or len(line) > 28 for line in lines):
        return False
    if any(any(char.isdigit() for char in line) or "%" in line for line in lines):
        return False
    mean = sum(len(line) for line in lines) / len(lines)
    words_each = [len(line.split()) for line in lines]
    short_names = sum(1 for count in words_each if count <= 2) >= len(lines) * 0.7
    return mean <= 18 and short_names and span < 170


def _looks_like_labels(lines: list[str]) -> bool:
    if not (3 <= len(lines) <= 16):
        return False
    if any(_is_url(line) or len(line) > 64 for line in lines):
        return False
    two_plus = sum(1 for line in lines if len(line.split()) >= 2)
    no_digit = sum(
        1 for line in lines if not any(char.isdigit() for char in line) and "%" not in line
    )
    return two_plus >= len(lines) * 0.45 and no_digit >= len(lines) * 0.7


def _looks_like_values(lines: list[str]) -> bool:
    if not (2 <= len(lines) <= 14):
        return False
    if any(_is_url(line) or len(line) > 36 for line in lines):
        return False
    mean = sum(len(line) for line in lines) / len(lines)
    compact = sum(
        1
        for line in lines
        if "%" in line or any(char.isdigit() for char in line) or len(line) <= 18
    )
    return mean <= 22 and compact >= max(2, int(len(lines) * 0.5))


def _is_status_line(line: str) -> bool:
    lower = line.lower()
    return len(line) > 40 or any(hint in lower for hint in _STATUS_HINTS)


def _format_paired_columns(columns: list[list[Word]], _page_width: int) -> str:
    """Zip a label column against a value column into heading + GFM table."""
    if len(columns) != 2:
        return ""
    left_rows = _plain_line_rows(columns[0])
    right_rows = _plain_line_rows(columns[1])
    left_lines = [text for _y, text in left_rows]
    right_lines = [text for _y, text in right_rows]
    if not (_looks_like_labels(left_lines) and _looks_like_values(right_lines)):
        return ""
    heights = [item[3] for item in columns[0] + columns[1]]
    median_h = sorted(heights)[len(heights) // 2] if heights else 16.0
    tol = max(14.0, min(28.0, median_h * 0.95))
    used_right: set[int] = set()
    pairs: list[tuple[str, str]] = []
    unmatched_left: list[str] = []
    for y, text in left_rows:
        match_j = None
        best = tol + 1
        for index, (right_y, _right_text) in enumerate(right_rows):
            if index in used_right:
                continue
            delta = abs(y - right_y)
            if delta < best:
                best = delta
                match_j = index
        if match_j is not None and best <= tol:
            used_right.add(match_j)
            pairs.append((text, right_rows[match_j][1]))
        else:
            unmatched_left.append(text)
    if len(pairs) < 2:
        return ""
    unmatched_right = [
        text for index, (_y, text) in enumerate(right_rows) if index not in used_right
    ]
    parts: list[str] = []
    if (
        unmatched_left
        and left_rows
        and unmatched_left[0] == left_rows[0][1]
        and _is_section_title(unmatched_left[0], first=True)
    ):
        parts.append(f"# {unmatched_left[0]}")
        unmatched_left = unmatched_left[1:]
    table_rows: list[list[str]] = []
    for left_text, right_text in pairs:
        if (
            not table_rows
            and "%" in right_text
            and not any(char.isdigit() for char in left_text)
        ):
            parts.append(left_text)
            parts.append(right_text)
            continue
        if _is_status_line(left_text):
            unmatched_left.append(left_text)
            unmatched_right.append(right_text)
            continue
        table_rows.append([left_text, right_text])
    if table_rows:
        parts.append(_gfm_table(table_rows))
    if unmatched_left:
        parts.append("\n".join(f"- {line}" for line in unmatched_left))
    if unmatched_right:
        parts.append("\n".join(unmatched_right))
    return "\n\n".join(part for part in parts if part)


def _is_section_title(line: str, *, first: bool) -> bool:
    if _is_url(line) or len(line) > 52:
        return False
    if _is_all_caps_heading(line):
        return True
    lower = line.lower()
    if lower.startswith("welcome ") or lower in {
        "what's new",
        "what's happening",
        "today's news",
    }:
        return True
    if first and line[:1].isupper() and not line.endswith("?") and not _is_status_line(line):
        words = line.split()
        count = len(words)
        if any(len(part) <= 1 for part in words if part.isalpha()):
            return False
        return 2 <= count <= 5 and not any(char.isdigit() for char in line)
    return False


def _gfm_table(rows: list[list[str]]) -> str:
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    if len(padded) == 1:
        return lines[0] + "\n" + lines[1]
    return "\n".join(lines)


def _format_chrome(words: list[Word]) -> str:
    if not words:
        return ""
    ordered = sorted(words, key=lambda item: (item[0], item[1]))
    return " ".join(word[4] for word in ordered)


def _plain_line_rows(words: list[Word]) -> list[tuple[float, str]]:
    if not words:
        return []
    ordered = sorted(words, key=lambda item: (item[0], item[1]))
    heights = sorted(item[3] for item in ordered)
    median_h = heights[len(heights) // 2] or 1.0
    y_tol = max(median_h * 0.7, 4.0)
    rows: list[list] = []
    for y, x, _w, height, token in ordered:
        if _is_noise_token(token):
            continue
        if not rows or abs(y - rows[-1][0]) > y_tol:
            rows.append([y, height, [(x, token)]])
        else:
            rows[-1][1] = max(rows[-1][1], height)
            rows[-1][2].append((x, token))
    return [
        (y, text)
        for y, _h, parts in rows
        if (text := _clean_ocr_line(" ".join(part[1] for part in sorted(parts))))
    ]


def _plain_lines(words: list[Word]) -> list[str]:
    return [text for _y, text in _plain_line_rows(words)]


def _format_lines(words: list[Word], *, span: float = 9999) -> str:
    lines = _plain_lines(words)
    if _looks_like_nav(lines, span=span):
        return "\n".join(f"- {line}" for line in lines)
    out: list[str] = []
    used_h1 = False
    for index, line in enumerate(lines):
        title = _is_section_title(line, first=False)
        if title:
            mark = "# " if not used_h1 else "## "
            used_h1 = True
            if out and out[-1] != "":
                out.append("")
            out.append(mark + line)
            out.append("")
        else:
            out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out).strip()


def _is_noise_token(text: str) -> bool:
    stripped = text.strip()
    if stripped.isdigit() or stripped.replace(":", "", 1).isdigit():
        return False
    if len(stripped) <= 2:
        return True
    return stripped in {"...", "×", "•", "·"}


def _format_column(words: list[Word], *, page_width: int = 0) -> str:
    del page_width
    span = _column_span(words)
    lines = _plain_lines(words)
    if _looks_like_nav(lines, span=span):
        return "\n".join(f"- {line}" for line in lines)
    blocks = _column_blocks(words)
    has_table = any(kind == "table" for kind, _payload in blocks)
    if not has_table and _looks_like_app_chrome(lines, span=span):
        return _format_app_chrome(lines)
    chunks: list[str] = []
    pending_plain: list[str] = []
    first_block = True
    peeled_percent = False

    def flush_plain() -> None:
        nonlocal pending_plain
        if not pending_plain:
            return
        if _looks_like_nav(pending_plain, span=span) or (
            len(pending_plain) >= 2 and all(_is_status_line(line) for line in pending_plain)
        ):
            chunks.append("\n".join(f"- {line}" for line in pending_plain))
        elif _looks_like_app_chrome(pending_plain, span=span):
            chunks.append(_format_app_chrome(pending_plain))
        else:
            chunks.append("\n".join(pending_plain))
        pending_plain = []

    for kind, payload in blocks:
        if kind == "table":
            flush_plain()
            rows = [list(row) for row in payload]  # type: ignore[arg-type]
            if (
                not peeled_percent
                and rows
                and "%" in rows[0][-1]
                and not any(char.isdigit() for char in rows[0][0])
            ):
                chunks.append(rows[0][0])
                chunks.append(rows[0][-1])
                rows = rows[1:]
                peeled_percent = True
            if rows:
                chunks.append(_gfm_table(rows))
            first_block = False
        else:
            text_lines = _plain_lines(payload)  # type: ignore[arg-type]
            if first_block and text_lines and _is_section_title(text_lines[0], first=True):
                chunks.append(f"# {text_lines[0]}")
                text_lines = text_lines[1:]
                first_block = False
            if text_lines:
                pending_plain.extend(text_lines)
                first_block = False
    flush_plain()
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()
