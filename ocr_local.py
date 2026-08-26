"""Local OCR — no cloud, no subscription.

Primary engine is RapidOCR with a Latin recognition model
(typically well under a second after warmup). Windows.Media.Ocr is
the fallback. Layout uses word/line boxes so three-column dashboards are
not read left-to-right.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from layout import words_to_markdown
from providers import ProviderError

# y, x, width, height, text
Word = tuple[float, float, float, float, str]

MODELS_DIR = Path.home() / ".snip2md" / "models"

_rapid_lock = threading.Lock()
_rapid_engine = None
_rapid_error: str | None = None


def warmup_ocr() -> None:
    """Load RapidOCR so the first snip is not a cold start."""
    try:
        _get_rapid_engine()
    except ProviderError:
        return


def image_to_markdown_ocr(
    pil_image: Image.Image,
    *,
    on_markdown: Callable[[str], None] | None = None,
) -> str:
    """Local OCR → Markdown.

    RapidOCR is emitted first (clipboard can copy immediately). Windows OCR
    runs next and replaces the result only when it is clearly better
    (typically Danish æøå).
    """
    rgb = pil_image.convert("RGB")
    rapid_error: BaseException | None = None
    rapid_words: list[Word] = []
    try:
        rapid_words = _rapid_words(rgb)
    except Exception as exc:
        rapid_error = exc
    if rapid_words:
        markdown = words_to_markdown(rapid_words, rgb.width, rgb.height)
        if markdown and on_markdown:
            on_markdown(markdown)
        windows_words = _try_windows_words(rgb)
        chosen = _prefer_words(rapid_words, windows_words)
        if chosen is not rapid_words:
            refined = words_to_markdown(chosen, rgb.width, rgb.height)
            if refined and refined != markdown:
                if on_markdown:
                    on_markdown(refined)
                markdown = refined
        return markdown
    try:
        text = _recognize_windows(rgb)
        if text and on_markdown:
            on_markdown(text)
        return text
    except ProviderError:
        if rapid_error is not None:
            raise ProviderError(
                "Local OCR failed. Try a larger region, or sign in and use AI polish."
            ) from rapid_error
        raise
    except Exception as exc:
        raise ProviderError(
            "Windows OCR failed. Install a language pack, or sign in and use AI."
        ) from exc


def _get_rapid_engine():
    global _rapid_engine, _rapid_error
    with _rapid_lock:
        if _rapid_engine is not None:
            return _rapid_engine
        if _rapid_error:
            raise ProviderError(_rapid_error)
        try:
            from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError as exc:
            _rapid_error = "Missing RapidOCR. Run: pip install -r requirements.txt"
            raise ProviderError(_rapid_error) from exc
        MODELS_DIR.mkdir(mode=0o700, exist_ok=True)
        try:
            _rapid_engine = RapidOCR(
                params={
                    "Global.model_root_dir": str(MODELS_DIR),
                    "Global.use_cls": False,
                    "Global.log_level": "error",
                    "Det.engine_type": EngineType.ONNXRUNTIME,
                    "Cls.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.lang_type": LangRec.LATIN,
                    "Rec.model_type": ModelType.MOBILE,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                }
            )
        except Exception as exc:
            _rapid_error = "RapidOCR could not load. Windows OCR will be used."
            raise ProviderError(_rapid_error) from exc
        return _rapid_engine


def _rapid_words(rgb: Image.Image) -> list[Word]:
    engine = _get_rapid_engine()
    result = engine(rgb)
    return _words_from_rapid(result)


def _diacritic_count(words: list[Word]) -> int:
    marks = "æøåäöüßéèêáàóòúùÆØÅÄÖÜÉ"
    return sum(char in marks for word in words for char in word[4])


def _ocr_quality(words: list[Word]) -> int:
    text = " ".join(word[4] for word in words)
    letters = sum(char.isalpha() for char in text)
    return _diacritic_count(words) * 8 + letters + min(len(words), 80)


def _prefer_words(rapid: list[Word], windows: list[Word]) -> list[Word]:
    if not windows:
        return rapid
    if not rapid:
        return windows
    if len(windows) < max(4, int(len(rapid) * 0.5)):
        return rapid
    if _diacritic_count(windows) >= _diacritic_count(rapid) + 2:
        return windows
    if _ocr_quality(windows) > _ocr_quality(rapid):
        return windows
    return rapid


def _try_windows_words(rgb: Image.Image) -> list[Word]:
    try:
        return asyncio.run(_windows_best_words(rgb))
    except Exception:
        return []


async def _windows_best_words(rgb: Image.Image) -> list[Word]:
    try:
        import winocr
    except ImportError:
        return []
    langs = _windows_ocr_languages()
    if not langs:
        return []
    best: list[Word] = []
    best_score = -1
    for lang in langs[:2]:
        try:
            result = await winocr.recognize_pil(rgb, lang)
        except Exception:
            continue
        if result is None:
            continue
        words = _words_from_windows(result)
        score = _ocr_quality(words)
        if score > best_score:
            best_score = score
            best = words
    return best


def _box_xywh(box) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    x = min(xs)
    y = min(ys)
    return y, x, max(xs) - x, max(ys) - y


def _keep_rapid_line(text: str, score: float, _box) -> bool:
    if not text or score < 0.72:
        return False
    if len(text) <= 2 and score < 0.93:
        return False
    return True


def _words_from_rapid(result) -> list[Word]:
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or txts is None:
        return []
    words: list[Word] = []
    for index, raw in enumerate(txts):
        text = str(raw or "").strip()
        score = float(scores[index]) if scores is not None else 1.0
        box = boxes[index]
        if not _keep_rapid_line(text, score, box):
            continue
        y, x, width, height = _box_xywh(box)
        words.append((y, x, max(width, 1.0), max(height, 1.0), text))
    return words


def _recognize_windows(rgb: Image.Image) -> str:
    try:
        return asyncio.run(_recognize_windows_async(rgb))
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(
            "Windows OCR failed. Install a language pack, or sign in and use AI."
        ) from exc


def _windows_ocr_languages() -> list[str]:
    """Language tags Windows has an OCR pack for — not a hardcoded locale list."""
    from winrt.windows.media.ocr import OcrEngine

    tags: list[str] = []
    seen: set[str] = set()

    def add(tag: object) -> None:
        value = str(tag or "").strip()
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        tags.append(value)

    try:
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is not None:
            add(getattr(engine.recognizer_language, "language_tag", None))
    except Exception:
        pass
    try:
        for item in OcrEngine.available_recognizer_languages:
            add(getattr(item, "language_tag", None))
    except Exception:
        pass
    return tags


async def _recognize_windows_async(rgb: Image.Image) -> str:
    try:
        import winocr
    except ImportError as exc:
        raise ProviderError(
            "Missing Windows OCR. Run: pip install -r requirements.txt"
        ) from exc

    langs = _windows_ocr_languages()
    if not langs:
        raise ProviderError(
            "Windows OCR has no language packs. Install one in Windows settings."
        )

    best = ""
    best_score = -1
    last_error: Exception | None = None
    for lang in langs:
        try:
            result = await winocr.recognize_pil(rgb, lang)
        except Exception as exc:
            last_error = exc
            continue
        if result is None:
            continue
        text = lines_to_markdown(result, rgb.width, rgb.height)
        score = sum(char.isalnum() for char in text)
        if score > best_score:
            best_score = score
            best = text
    if best_score < 0:
        raise ProviderError("Windows OCR could not read that region.") from last_error
    if not best:
        raise ProviderError("OCR found no text in that region.")
    return best


def _words_from_windows(result) -> list[Word]:
    """One box per OCR line so short words (du, vi, og) are not dropped."""
    words: list[Word] = []
    for line in getattr(result, "lines", None) or ():
        token = str(getattr(line, "text", "") or "").strip()
        if not token:
            continue
        boxes: list[tuple[float, float, float, float]] = []
        for word in getattr(line, "words", None) or ():
            box = getattr(word, "bounding_rect", None)
            if box is None:
                continue
            boxes.append(
                (
                    float(getattr(box, "y", 0.0) or 0.0),
                    float(getattr(box, "x", 0.0) or 0.0),
                    float(getattr(box, "width", 0.0) or 8.0),
                    float(getattr(box, "height", 0.0) or 12.0),
                )
            )
        if boxes:
            top = min(item[0] for item in boxes)
            left = min(item[1] for item in boxes)
            right = max(item[1] + item[2] for item in boxes)
            bottom = max(item[0] + item[3] for item in boxes)
            words.append((top, left, max(right - left, 1.0), max(bottom - top, 1.0), token))
        else:
            words.append((float(len(words) * 16), 0.0, 8.0, 12.0, token))
    return words


def lines_to_markdown(result, width: int = 0, height: int = 0) -> str:
    words = _words_from_windows(result)
    if not words:
        return str(getattr(result, "text", "") or "").strip()
    return words_to_markdown(words, width, height)
