"""Geometry-only layout for OCR boxes → GitHub-flavored Markdown.

No per-document rules. Reading order follows XY-Cut++ (arXiv:2504.10258 /
MinerU): mask cross-layout banners (wide boxes that overlap two or more
estimated columns), then k-way-split the rest on every vertical gutter —
not only the largest. Two similar gutters are a 3-column grid; a single
dominant gutter is two reading columns. A skinny price rail merges into
the column on its left. Banners are reinserted by Y. On a card grid,
chrome above the first aligned row (logo, kicker, title) is one header
in Y order; the visually dominant line is H1, not the first ALL-CAPS word.
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
    pre_banners, columns, footers = _split_page_columns(body, width, height)
    parts: list[str] = []
    chrome = _format_chrome(header)
    if chrome:
        parts.append(chrome)
    if _columns_form_grid(columns):
        pre_banners, columns, lifted_foot = _lift_above_grid(
            columns, pre_banners
        )
        footers = lifted_foot + footers
        header_md = (
            _format_header_band(pre_banners, page_width=width)
            if pre_banners
            else ""
        )
        if header_md:
            parts.append(header_md)
        grid_md = _format_column(
            [word for column in columns for word in column],
            page_width=width,
        )
        if grid_md:
            parts.append(grid_md)
    else:
        pre_md = (
            _format_column(pre_banners, page_width=width) if pre_banners else ""
        )
        if pre_md:
            parts.append(pre_md)
        paired = _format_paired_columns(columns, width)
        if paired:
            parts.append(paired)
        else:
            for column in columns:
                chunk = _format_column(column, page_width=width)
                if chunk:
                    parts.append(chunk)
    foot_md = _format_column(footers, page_width=width) if footers else ""
    if foot_md:
        parts.append(foot_md)
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


def _is_spanning_banner(word: Word, width: float) -> bool:
    """True for a full-width band that sits in the column gutter (footer, banner)."""
    _y, x, w, _h, _text = word
    if width <= 0 or w < width * 0.28:
        return False
    center = x + w / 2.0
    if center < width * 0.40 or center > width * 0.60:
        return False
    return x < width * 0.42 and (x + w) > width * 0.58


def _word_center_x(word: Word) -> float:
    return word[1] + word[2] / 2.0


def _x_center_cuts(words: list[Word], width: int) -> list[float]:
    """Gutters between x-center clusters. Two cuts means a 3-column grid."""
    if len(words) < 6 or width <= 0:
        return []
    centers = sorted(_word_center_x(word) for word in words)
    threshold = max(44.0, width * 0.06)
    cuts: list[float] = []
    for index in range(len(centers) - 1):
        gap = centers[index + 1] - centers[index]
        if gap >= threshold:
            cuts.append((centers[index] + centers[index + 1]) / 2.0)
    return cuts


def _split_by_center_gap(words: list[Word], width: int) -> list[list[Word]]:
    """K-way split on large x-center gutters when occupancy cannot see them.

    Equal gutters all cut (3-column grid). A box-edge overlap is not a gutter
    — that is wrapped text in one column, not a new page column.
    """
    return _kway_from_centers(words, width)


def _column_cut(left: list[Word], right: list[Word]) -> float:
    left_edge = max(word[1] + word[2] for word in left)
    right_edge = min(word[1] for word in right)
    if right_edge > left_edge:
        return (left_edge + right_edge) / 2.0
    return (
        max(_word_center_x(word) for word in left)
        + min(_word_center_x(word) for word in right)
    ) / 2.0


def _cluster_by_x_cuts(words: list[Word], cuts: list[float]) -> list[list[Word]]:
    groups: list[list[Word]] = [[] for _ in range(len(cuts) + 1)]
    for word in words:
        slot = 0
        center = _word_center_x(word)
        for cut in cuts:
            if center < cut:
                break
            slot += 1
        groups[slot].append(word)
    return [group for group in groups if group]


def _kway_from_centers(words: list[Word], width: int) -> list[list[Word]]:
    """Cluster on x-center gutters, then drop cuts whose box edges still overlap."""
    if len(words) < 6 or width <= 0:
        return [words]
    cuts = _x_center_cuts(words, width)
    if not cuts:
        return [words]
    min_edge = max(28.0, width * 0.035)
    slots: list[list[Word]] = [[] for _ in range(len(cuts) + 1)]
    for word in words:
        slot = 0
        center = _word_center_x(word)
        for cut in cuts:
            if center < cut:
                break
            slot += 1
        slots[slot].append(word)
    kept: list[float] = []
    for index, cut in enumerate(cuts):
        left, right = slots[index], slots[index + 1]
        if not left or not right:
            continue
        gap = min(word[1] for word in right) - max(
            word[1] + word[2] for word in left
        )
        if gap >= min_edge:
            kept.append(cut)
    if not kept:
        return [words]
    clustered = _cluster_by_x_cuts(words, kept)
    return clustered if len(clustered) >= 2 else [words]


def _median_width(words: list[Word]) -> float:
    widths = sorted(word[2] for word in words)
    return widths[len(widths) // 2] if widths else 40.0


def _column_x_ranges(groups: list[list[Word]]) -> list[tuple[float, float]]:
    return [
        (min(word[1] for word in group), max(word[1] + word[2] for word in group))
        for group in groups
        if group
    ]


def _overlaps_ranges(word: Word, ranges: list[tuple[float, float]]) -> int:
    left, right = word[1], word[1] + word[2]
    hits = 0
    for start, end in ranges:
        if left < end - 4 and right > start + 4:
            hits += 1
    return hits


def _merge_x_ranges(
    ranges: list[tuple[float, float]], pad: float
) -> list[tuple[float, float]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[list[float]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + pad:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _estimate_column_ranges(words: list[Word], width: int) -> list[tuple[float, float]]:
    """Major column x-ranges from narrow, non-banner boxes."""
    if len(words) < 4 or width <= 0:
        return []
    candidates = [
        word for word in words if not _is_spanning_banner(word, width)
    ] or words
    page_narrow = width * 0.34
    median_w = _median_width(candidates)
    narrow = [
        word
        for word in candidates
        if word[2] < max(median_w * 1.45, page_narrow)
    ]
    if len(narrow) < 4:
        narrow = candidates
    min_gap = max(24, int(width * 0.03))
    groups = _split_groups(narrow, axis="x", min_gap=min_gap, shrink=0.9)
    if len(groups) < 2:
        groups = _kway_from_centers(narrow, width)
    if len(groups) < 2:
        return []
    pad = max(12.0, width * 0.015)
    ranges = _merge_x_ranges(_column_x_ranges(groups), pad)
    return ranges if len(ranges) >= 2 else []


def _partition_cross_layout(
    words: list[Word], width: int
) -> tuple[list[Word], list[Word]]:
    """XY-Cut++ pre-mask: wide boxes that sit across two estimated columns."""
    if len(words) < 4 or width <= 0:
        return [], words
    ranges = _estimate_column_ranges(words, width)
    if len(ranges) < 2:
        spanning = [word for word in words if _is_spanning_banner(word, width)]
        core = [word for word in words if word not in spanning]
        return spanning, core or words
    median_w = _median_width(words)
    wide_cut = max(median_w * 1.5, width * 0.36)
    cross: list[Word] = []
    core: list[Word] = []
    for word in words:
        wide = word[2] >= wide_cut
        straddles = _overlaps_ranges(word, ranges) >= 2
        if (wide and straddles) or _is_spanning_banner(word, width):
            cross.append(word)
        else:
            core.append(word)
    if not core:
        return [], words
    return cross, core


def _is_price_rail(group: list[Word]) -> bool:
    if not group:
        return False
    prices = sum(1 for word in group if _is_price_line(word[4]))
    return prices >= max(1, int(len(group) * 0.45))


def _merge_price_rails(groups: list[list[Word]]) -> list[list[Word]]:
    """A skinny column of prices belongs to the column on its left."""
    if len(groups) < 3:
        return groups
    merged: list[list[Word]] = []
    for group in groups:
        if merged and _is_price_rail(group):
            merged[-1] = merged[-1] + group
        else:
            merged.append(list(group))
    return merged


def _extract_straddlers(
    columns: list[list[Word]], width: int
) -> tuple[list[list[Word]], list[Word]]:
    """Boxes that still sit across two page columns are banners, not cells."""
    if len(columns) < 2:
        return columns, []
    ranges = _column_x_ranges(columns)
    kept: list[list[Word]] = [[] for _ in columns]
    straddlers: list[Word] = []
    for index, column in enumerate(columns):
        for word in column:
            if _overlaps_ranges(word, ranges) >= 2 and word[2] >= width * 0.22:
                straddlers.append(word)
            else:
                kept[index].append(word)
    return [column for column in kept if column], straddlers


def _column_extent_h(group: list[Word]) -> float:
    return max(word[0] + word[3] for word in group) - min(word[0] for word in group)


def _coarsen_reading_columns(groups: list[list[Word]]) -> list[list[Word]]:
    """Keep tall reading columns; merge adjacent short card clusters into one region.

    Three metric cards plus a sidebar is two page columns (the cards become a
    table inside the main column). Three tall speaker columns stay three.
    """
    if len(groups) <= 2:
        return groups
    ordered = sorted(groups, key=lambda group: min(word[1] for word in group))
    top = min(word[0] for group in ordered for word in group)
    bot = max(word[0] + word[3] for group in ordered for word in group)
    content_h = max(bot - top, 1.0)

    def tall(group: list[Word]) -> bool:
        return _column_extent_h(group) >= 0.42 * content_h

    packed: list[list[Word]] = [list(ordered[0])]
    for group in ordered[1:]:
        if (not tall(packed[-1])) and (not tall(group)):
            packed[-1].extend(group)
        else:
            packed.append(list(group))
    return packed


def _band_x_cuts(band: list[Word], width: int) -> list[float]:
    min_gap = max(24, int(width * 0.03))
    groups = _split_groups(band, axis="x", min_gap=min_gap, shrink=0.9)
    if len(groups) < 2:
        groups = _kway_from_centers(band, width)
    if len(groups) < 2:
        return []
    ordered = sorted(groups, key=lambda group: min(word[1] for word in group))
    cuts: list[float] = []
    for index in range(len(ordered) - 1):
        left_edge = max(word[1] + word[2] for word in ordered[index])
        right_edge = min(word[1] for word in ordered[index + 1])
        if right_edge - left_edge >= min_gap * 0.5:
            cuts.append((left_edge + right_edge) / 2.0)
    return cuts


def _through_x_cuts(words: list[Word], width: int) -> list[float]:
    """Vertical gutters that run through most of the content, not one card row."""
    if len(words) < 6 or width <= 0:
        return []
    top = min(word[0] for word in words)
    bot = max(word[0] + word[3] for word in words)
    content_h = max(bot - top, 1.0)
    median_h = _median_height(words)
    strip_h = max(median_h * 3.0, content_h / 8.0, 40.0)
    seen: list[list[float]] = []
    y = top
    while y < bot:
        band = [
            word
            for word in words
            if word[0] < y + strip_h and word[0] + word[3] > y
        ]
        y += strip_h
        if len(band) < 2:
            continue
        cuts = _band_x_cuts(band, width)
        if cuts:
            seen.append(cuts)
    if len(seen) < 2:
        return []
    tol = max(20.0, width * 0.045)
    buckets: list[list[float]] = []
    for cuts in seen:
        for cut in cuts:
            matched = False
            for bucket in buckets:
                center = sum(bucket) / len(bucket)
                if abs(cut - center) <= tol:
                    bucket.append(cut)
                    matched = True
                    break
            if not matched:
                buckets.append([cut])
    needed = max(2, int(round(len(seen) * 0.6)))
    through = [
        sum(bucket) / len(bucket)
        for bucket in buckets
        if len(bucket) >= needed
    ]
    return sorted(through)


def _kway_x_columns(words: list[Word], width: int) -> list[list[Word]]:
    if not words:
        return []
    cuts = _through_x_cuts(words, width)
    if cuts:
        groups = _cluster_by_x_cuts(words, cuts)
        if len(groups) >= 2:
            return _merge_price_rails(groups)
    min_gap = max(28, int(width * 0.04))
    groups = _split_groups(words, axis="x", min_gap=min_gap, shrink=0.9)
    if len(groups) < 2:
        groups = _kway_from_centers(words, width)
    if len(groups) < 2:
        return [words]
    groups = _coarsen_reading_columns(groups)
    return _merge_price_rails(groups)


def _aligned_row_ys(columns: list[list[Word]]) -> list[float]:
    if len(columns) < 2:
        return []
    heights = [word[3] for column in columns for word in column]
    median_h = sorted(heights)[len(heights) // 2] if heights else 16.0
    tol = max(14.0, median_h * 1.6)
    row_ys: list[float] = []
    for word in columns[0]:
        hits = 1
        for other in columns[1:]:
            if any(abs(item[0] - word[0]) <= tol for item in other):
                hits += 1
        if hits == len(columns):
            row_ys.append(word[0])
    return row_ys


def _columns_form_grid(columns: list[list[Word]]) -> bool:
    """True when k columns are a card/photo grid (row-major table), not articles."""
    if not (3 <= len(columns) <= 4):
        return False
    if any(_is_price_rail(column) for column in columns):
        return False
    counts = [len(column) for column in columns]
    if min(counts) < 1:
        return False
    if max(counts) > 2.4 * min(counts):
        return False
    heights = [word[3] for column in columns for word in column]
    median_h = sorted(heights)[len(heights) // 2] if heights else 16.0
    row_ys = _aligned_row_ys(columns)
    aligned_items = len(row_ys) * len(columns)
    total = sum(counts)
    if total and aligned_items / total < 0.35:
        return False
    if len(row_ys) >= 2:
        ordered = sorted(row_ys)
        gaps = [
            ordered[index + 1] - ordered[index] for index in range(len(ordered) - 1)
        ]
        return max(gaps) >= median_h * 3.0
    if len(row_ys) == 1:
        return min(counts) >= 1
    return False


def _lift_above_grid(
    columns: list[list[Word]], banners: list[Word]
) -> tuple[list[Word], list[list[Word]], list[Word]]:
    """Pull logo/kicker/title above the first card row into one Y-ordered header."""
    row_ys = _aligned_row_ys(columns)
    if not row_ys:
        return banners, columns, []
    grid_y = min(row_ys)
    heights = [word[3] for column in columns for word in column]
    median_h = sorted(heights)[len(heights) // 2] if heights else 16.0
    clear = max(8.0, median_h * 0.55)
    above: list[Word] = []
    kept: list[list[Word]] = []
    for column in columns:
        leftover: list[Word] = []
        for word in column:
            if word[0] + word[3] <= grid_y - clear:
                above.append(word)
            else:
                leftover.append(word)
        if leftover:
            kept.append(leftover)
    post: list[Word] = []
    for word in banners:
        if word[0] + word[3] <= grid_y - clear:
            above.append(word)
        else:
            post.append(word)
    above.sort(key=lambda word: (word[0], word[1]))
    return above, kept or columns, post


def _split_page_columns(
    words: list[Word], width: int, height: int
) -> tuple[list[Word], list[list[Word]], list[Word]]:
    """Mask cross-layout banners, then k-way split the remaining columns."""
    del height
    if not words:
        return [], [], []
    cross, core = _partition_cross_layout(words, width)
    columns = _kway_x_columns(core, width)
    if not columns:
        columns = [core]
    columns, straddlers = _extract_straddlers(columns, width)
    if not columns:
        columns = [core]
    pre, post = _place_banners(cross + straddlers, columns)
    return pre, columns, post


def _place_banners(
    banners: list[Word], columns: list[list[Word]]
) -> tuple[list[Word], list[Word]]:
    if not banners:
        return [], []
    placed = [word for column in columns for word in column]
    if not placed:
        return [], banners
    col_top = min(word[0] for word in placed)
    mid = sorted(word[0] for word in placed)[len(placed) // 2]
    pre: list[Word] = []
    post: list[Word] = []
    for word in banners:
        y, _x, _w, h, _text = word
        center_y = y + h / 2.0
        if y + h <= col_top + 12 or center_y <= mid:
            pre.append(word)
        else:
            post.append(word)
    return pre, post


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
    """Return ('lines'|'section'|'table', payload) in reading order.

    Independent two-cell bands (menu columns) are accumulated left-then-right
    instead of emitting each band as left cell then right cell.
    """
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
    left_words: list[Word] = []
    right_words: list[Word] = []
    foot_words: list[Word] = []

    def flush_table() -> None:
        nonlocal pending_rows
        if not pending_rows:
            return
        if len(pending_rows) == 1:
            pending_rows = []
            return
        blocks.append(("table", pending_rows))
        pending_rows = []

    def flush_dual() -> None:
        nonlocal left_words, right_words, foot_words
        if left_words:
            blocks.append(("section", _merge_paragraph(left_words)))
        if right_words:
            blocks.append(("section", _merge_paragraph(right_words)))
        if foot_words:
            blocks.append(("lines", _merge_paragraph(foot_words)))
        left_words = []
        right_words = []
        foot_words = []

    def dual_cut() -> float | None:
        if not left_words or not right_words:
            return None
        return _column_cut(left_words, right_words)

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
        independent = False
        price_pair = False
        if len(cells) == 2:
            left_lines = [
                item[4]
                for item in _merge_paragraph(
                    sorted(cells[0], key=lambda item: (item[0], item[1]))
                )
            ]
            right_lines = [
                item[4]
                for item in _merge_paragraph(
                    sorted(cells[1], key=lambda item: (item[0], item[1]))
                )
            ]
            left_span = _column_span(cells[0])
            right_span = _column_span(cells[1])
            wide_narrow = min(left_span, right_span) > 0 and (
                max(left_span, right_span) >= 1.55 * min(left_span, right_span)
            )
            independent = wide_narrow or _looks_like_independent_columns(
                left_lines, right_lines
            )
            one_each = len(left_lines) == 1 and len(right_lines) == 1
            if one_each and not price_pair:
                independent = True
            right_prices = sum(1 for line in right_lines if _is_price_line(line))
            if right_prices >= max(1, int(len(right_lines) * 0.5)) and not any(
                _is_price_line(line) for line in left_lines
            ):
                independent = False
                price_pair = True
        use_table = (
            ((3 <= len(cells) <= 4) or (both_real and not independent))
            and not price_pair
        )
        if independent and len(cells) == 2:
            flush_table()
            left_words.extend(cells[0])
            right_words.extend(cells[1])
            continue
        if left_words or right_words:
            cut = dual_cut()
            center = sum(_word_center_x(item) for item in band) / len(band)
            span = _column_span(band)
            page_span = _column_span(words)
            page_max_y = max(item[0] + item[3] for item in words)
            band_y = min(item[0] for item in band)
            near_bottom = band_y >= page_max_y * 0.82
            if cut is not None and near_bottom and (
                span >= page_span * 0.45
                or abs(center - (min(item[1] for item in words) + page_span / 2.0))
                < page_span * 0.2
            ):
                foot_words.extend(band)
            elif cut is not None and center >= cut:
                right_words.extend(band)
            else:
                left_words.extend(band)
            continue
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
    flush_dual()
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
        indented = 0 <= (x - px) <= 28
        close_y = 0 <= (y - (py + ph)) <= max(ph, h) * 1.35
        if _is_url(text):
            merged.append(word)
            continue
        next_item = _list_kind(text)
        prev_item = _list_kind(ptext)
        if next_item:
            merged.append(word)
            continue
        continuation = len(text) <= 24 and (
            not text[:1].isupper() or text.endswith("?")
        )
        first_is_prose = len(ptext) >= 28
        new_item = bool(re.match(r"^\d+\.", text.strip())) or text.strip().startswith(
            ("(V)", "(v)")
        )
        prev_done = ptext.rstrip().endswith((".", "!", "?"))
        wrapped_item = (
            (same_left or indented)
            and close_y
            and not new_item
            and not prev_done
            and not _is_url(text)
            and not _is_price_line(text)
            and len(ptext) >= 32
            and not text[:1].isupper()
        )
        list_wrap = (
            prev_item is not None
            and close_y
            and (same_left or indented)
            and not prev_done
            and not text[:1].isupper()
        )
        price_under_label = (
            same_left
            and close_y
            and _is_price_line(text)
            and len(ptext.split()) <= 4
            and not _is_price_line(ptext)
        )
        if (same_left and close_y and continuation and first_is_prose and not text[:1].isupper()) or wrapped_item or list_wrap or price_under_label:
            merged[-1] = (py, px, max(pw, x + w - px), y + h - py, f"{ptext} {text}")
        else:
            merged.append(word)
    return merged


def _list_kind(text: str) -> str | None:
    """'num' (1. …), 'bullet' (OCR dot/disk), or None."""
    stripped = text.strip()
    if not stripped:
        return None
    if re.match(r"^\d+\.\s*\S", stripped):
        return "num"
    if re.match(r"^[\(\[]?\s*[·•●▪◦⦁∙]\s*\S", stripped):
        return "bullet"
    if re.match(r"^[-*–—]\s+\S", stripped):
        return "bullet"
    if re.match(r"^\.\s+\S", stripped):
        return "bullet"
    return None


def _strip_list_prefix(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^[\(\[]?\s*[·•●▪◦⦁∙]\s*", "", stripped)
    stripped = re.sub(r"^[-*–—]\s+", "", stripped)
    stripped = re.sub(r"^\.\s+", "", stripped)
    return stripped.strip()


def _as_markdown_item(text: str) -> str:
    kind = _list_kind(text)
    if kind == "bullet":
        return f"- {_strip_list_prefix(text)}"
    return text.strip()


def _is_url(line: str) -> bool:
    lower = line.lower()
    return lower.startswith("http://") or lower.startswith("https://")
    lower = line.lower()
    return lower.startswith("http://") or lower.startswith("https://")


def _is_email(line: str) -> bool:
    return "@" in line and "." in line.split("@")[-1]


def _clean_ocr_line(line: str) -> str:
    return line.strip("()[]{} \t")


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
    if any(_list_kind(line) for line in lines):
        return False
    if any(
        _is_price_line(line) or re.search(r"\d[\d.,]*\s*kr", line, flags=re.I)
        for line in lines
    ):
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
    if _is_email(line) or _is_url(line):
        return False
    had_price = bool(re.search(r"\d", line))
    core = re.sub(r"\s+\d[\d.,]*\s*kr\.?-?\s*$", "", line, flags=re.I).strip()
    words = core.split()
    if had_price and len(words) != 1:
        return False
    if not (1 <= len(words) <= 3):
        return False
    letters = "".join(char for char in core if char.isalpha())
    if len(letters) < 4 or len(core) > 42:
        return False
    return letters.isupper()




def _column_span(words: list[Word]) -> float:
    if not words:
        return 0.0
    left = min(item[1] for item in words)
    right = max(item[1] + item[2] for item in words)
    return right - left


def _is_price_line(line: str) -> bool:
    compact = line.strip().lower().replace(" ", "")
    return bool(re.match(r"^\d[\d.,]*kr\.?-?$", compact))


def _looks_like_independent_columns(left: list[str], right: list[str]) -> bool:
    """Two reading columns (menu, article), not a label|value pair."""
    if not left or not right:
        return False
    left_mean = sum(len(line) for line in left) / len(left)
    right_mean = sum(len(line) for line in right) / len(right)
    left_long = sum(1 for line in left if len(line) >= 28) >= max(1, int(len(left) * 0.3))
    return left_long and left_mean >= 22 and right_mean <= 26


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
    """Prose / sentence, not a short label. No vocabulary list."""
    if len(line) > 40:
        return True
    return len(line.split()) >= 6


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
    if _looks_like_independent_columns(left_lines, right_lines):
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
    if _is_url(line) or _is_email(line) or len(line) > 52 or "+" in line:
        return False
    if _is_all_caps_heading(line):
        return True
    if first and line[:1].isupper() and not line.endswith("?") and not _is_status_line(line):
        words = line.split()
        count = len(words)
        if any(len(part) <= 1 for part in words if part.isalpha()):
            return False
        titled = all(part[:1].isupper() for part in words if part.isalpha())
        return 2 <= count <= 5 and titled and not any(char.isdigit() for char in line)
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


def _dominant_header_word(words: list[Word], page_width: int) -> str | None:
    """Widest tall line in the header that is not a sentence — the poster title."""
    if len(words) < 2:
        return None
    usable = [
        word
        for word in words
        if not _is_email(word[4])
        and not _is_url(word[4])
        and not _is_status_line(word[4])
    ]
    if not usable:
        return None
    max_h = max(word[3] for word in usable)
    tall = [word for word in usable if word[3] >= max_h * 0.82]
    top = min(word[0] for word in words)
    bot = max(word[0] + word[3] for word in words)
    upper = [
        word for word in tall if word[0] <= top + 0.55 * max(bot - top, 1.0)
    ]
    pool = upper or tall
    best = max(pool, key=lambda word: word[2])
    median_w = _median_width(words)
    min_w = max(median_w * 1.65, page_width * 0.28 if page_width else 0.0, 90.0)
    if best[2] < min_w:
        return None
    return best[4].strip()


def _format_header_band(words: list[Word], *, page_width: int = 0) -> str:
    """Logo/kicker, then the dominant title as H1, then subtitle — Y order."""
    if not words:
        return ""
    merged = _merge_paragraph(sorted(words, key=lambda item: (item[0], item[1])))
    dominant = _dominant_header_word(merged, page_width)
    rows = _plain_line_rows(merged)
    if not rows:
        return ""
    if not dominant:
        return _format_text_lines(
            [text for _y, text in rows], span=_column_span(words)
        )
    before: list[str] = []
    after: list[str] = []
    seen_title = False
    for _y, text in rows:
        if not seen_title and text == dominant:
            seen_title = True
            continue
        if not seen_title:
            before.append(text)
        else:
            after.append(text)
    parts: list[str] = []
    if before:
        parts.append("\n".join(before))
    parts.append(f"# {dominant}")
    if after:
        rest: list[str] = []
        for line in after:
            if _is_all_caps_heading(line):
                if rest:
                    parts.append("\n".join(rest))
                    rest = []
                parts.append(f"## {line}")
            else:
                rest.append(line)
        if rest:
            parts.append("\n".join(rest))
    return "\n\n".join(parts).strip()


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


def _format_text_lines(
    lines: list[str], *, span: float = 9999, used_h1: bool = False
) -> str:
    raw = [line.strip() for line in lines if line.strip()]
    bodies = [
        _strip_list_prefix(line) if _list_kind(line) == "bullet" else line
        for line in raw
    ]
    if _looks_like_nav(bodies, span=span):
        return "\n".join(f"- {body}" for body in bodies)
    force_list = sum(1 for line in raw if _list_kind(line) == "bullet") >= 2
    out: list[str] = []
    list_run = 0
    for line in raw:
        item = _as_markdown_item(line)
        is_item = item.startswith("- ") or (
            force_list
            and not _is_email(line)
            and not _is_url(line)
            and not _is_all_caps_heading(line)
            and not _is_section_title(line, first=not used_h1)
            and not line.endswith("?")
            and not re.search(r"\d", line)
            and not _is_price_line(line)
            and _list_kind(line) != "num"
        )
        if is_item:
            body = item[2:] if item.startswith("- ") else line
            if (
                list_run
                and out
                and out[-1].startswith("- ")
                and not line[:1].isupper()
                and _list_kind(line) is None
            ):
                out[-1] = f"{out[-1]} {body}"
                continue
            out.append(f"- {body}" if not item.startswith("- ") else item)
            list_run += 1
            continue
        list_run = 0
        if _is_email(line):
            out.append(line)
            continue
        title = _is_section_title(line, first=not used_h1)
        if title or _is_all_caps_heading(line):
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


def _format_lines(words: list[Word], *, span: float = 9999) -> str:
    return _format_text_lines(_plain_lines(words), span=span)


def _is_noise_token(text: str) -> bool:
    stripped = text.strip()
    if stripped.isalpha():
        return False
    if stripped in {".", "·", "•", "●", "▪", "◦", "-", "*", "–", "—"}:
        return False
    if stripped.isdigit() or stripped.replace(":", "", 1).isdigit():
        return False
    if len(stripped) <= 2:
        return True
    return stripped in {"...", "×"}


def _format_column(words: list[Word], *, page_width: int = 0) -> str:
    del page_width
    span = _column_span(words)
    lines = _plain_lines(words)
    if _looks_like_nav(lines, span=span):
        return "\n".join(
            _as_markdown_item(line)
            if _list_kind(line)
            else f"- {line}"
            for line in lines
        )
    blocks = _column_blocks(words)
    has_table = any(kind == "table" for kind, _payload in blocks)
    if not has_table and _looks_like_app_chrome(lines, span=span):
        return _format_app_chrome(lines)
    chunks: list[str] = []
    pending_plain: list[str] = []
    first_block = True
    peeled_percent = False
    used_h1 = False

    def flush_plain() -> None:
        nonlocal pending_plain, used_h1
        if not pending_plain:
            return
        numbered = any(re.match(r"^\d+\.", line.strip()) for line in pending_plain)
        if _looks_like_nav(pending_plain, span=span) or (
            len(pending_plain) >= 2
            and all(_is_status_line(line) for line in pending_plain)
            and not numbered
        ):
            chunks.append("\n".join(f"- {line}" for line in pending_plain))
        elif _looks_like_app_chrome(pending_plain, span=span):
            chunks.append(_format_app_chrome(pending_plain))
        else:
            chunks.append(
                _format_text_lines(pending_plain, span=span, used_h1=used_h1)
            )
            used_h1 = used_h1 or any(
                line.startswith("#") for line in chunks[-1].splitlines()
            )
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
        elif kind == "section":
            flush_plain()
            used_h1 = False
            text_lines = _plain_lines(payload)  # type: ignore[arg-type]
            if text_lines and _is_section_title(text_lines[0], first=True) and not _is_email(text_lines[0]):
                chunks.append(f"# {text_lines[0]}")
                text_lines = text_lines[1:]
                used_h1 = True
            if text_lines:
                pending_plain.extend(text_lines)
            first_block = False
        else:
            text_lines = _plain_lines(payload)  # type: ignore[arg-type]
            if (
                first_block
                and not has_table
                and text_lines
                and _is_section_title(text_lines[0], first=True)
                and not _is_email(text_lines[0])
            ):
                chunks.append(f"# {text_lines[0]}")
                text_lines = text_lines[1:]
                first_block = False
                used_h1 = True
            if text_lines:
                pending_plain.extend(text_lines)
                first_block = False
    flush_plain()
    return "\n\n".join(chunk for chunk in chunks if chunk).strip()
