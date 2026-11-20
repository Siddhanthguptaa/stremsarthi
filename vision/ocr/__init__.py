"""Shared OCR service for the ACCURATE lane (``contracts.OcrProvider``). See ``service.py``."""

from .service import OcrService, OcrTiming, OcrUnavailableError, build_ocr

__all__ = ["OcrService", "OcrTiming", "OcrUnavailableError", "build_ocr"]
