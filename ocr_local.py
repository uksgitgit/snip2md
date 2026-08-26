"""Local OCR — no cloud, no subscription.

Primary engine is RapidOCR with a Latin recognition model (Danish + English
UI text, typically well under a second after warmup). Windows.Media.Ocr is
the fallback. Layout uses word/line boxes so three-column dashboards are
not read left-to-right.
"""

from __future__ import annotations

import asyncio
import threading
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


def image_to_markdown_ocr(pil_image: Image.Image) -> str:
    rgb = pil_image.convert("RGB")
    rapid_error: BaseException | None = None
    try:
        text = _recognize_rapid(rgb)
        if text:
            return text
    except Exception as exc:
        rapid_error = exc
    try:
        return _recognize_windows(rgb)
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


def _recognize_rapid(rgb: Image.Image) -> str:
    engine = _get_rapid_engine()
    result = engine(rgb)
    words = _words_from_rapid(result)
    if not words:
        return ""
    return words_to_markdown(words, rgb.width, rgb.height)


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
        text = text.replace("ä", "å").replace("Ä", "Å")
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


async def _recognize_windows_async(rgb: Image.Image) -> str:
    try:
        import winocr
    except ImportError as exc:
        raise ProviderError(
            "Missing Windows OCR. Run: pip install -r requirements.txt"
        ) from exc

    result = None
    last_error: Exception | None = None
    for lang in ("da", "da-DK", "en-US"):
        try:
            result = await winocr.recognize_pil(rgb, lang)
            if result is not None:
                break
        except Exception as exc:
            last_error = exc
            continue
    if result is None:
        raise ProviderError("Windows OCR could not read that region.") from last_error
    text = lines_to_markdown(result, rgb.width, rgb.height)
    if not text:
        raise ProviderError("OCR found no text in that region.")
    return text


def _words_from_windows(result) -> list[Word]:
    words: list[Word] = []
    for line in getattr(result, "lines", None) or ():
        for word in getattr(line, "words", None) or ():
            token = str(getattr(word, "text", "") or "").strip()
            if not token:
                continue
            box = getattr(word, "bounding_rect", None)
            if box is None:
                words.append((0.0, float(len(words)), 8.0, 12.0, token))
                continue
            words.append(
                (
                    float(getattr(box, "y", 0.0) or 0.0),
                    float(getattr(box, "x", 0.0) or 0.0),
                    float(getattr(box, "width", 0.0) or 8.0),
                    float(getattr(box, "height", 0.0) or 12.0),
                    token,
                )
            )
    return words


def lines_to_markdown(result, width: int = 0, height: int = 0) -> str:
    words = _words_from_windows(result)
    if not words:
        return str(getattr(result, "text", "") or "").strip()
    return words_to_markdown(words, width, height)
