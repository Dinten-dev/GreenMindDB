"""Validation helpers for untrusted observation image uploads."""

from __future__ import annotations

import warnings
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

_ALLOWED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_MAX_IMAGE_DIMENSION = 20_000
_MAX_IMAGE_PIXELS = 40_000_000


def validate_observation_image(file_data: BinaryIO) -> str:
    """Decode-validate an image and return its canonical MIME type."""
    file_data.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(file_data) as image:
                detected_format = image.format
                if detected_format not in _ALLOWED_FORMATS:
                    raise ValueError("Image must be JPEG, PNG, or WebP")
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > _MAX_IMAGE_DIMENSION
                    or height > _MAX_IMAGE_DIMENSION
                    or width * height > _MAX_IMAGE_PIXELS
                ):
                    raise ValueError("Image dimensions exceed the allowed limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Animated images are not allowed")
                image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
    ) as exc:
        raise ValueError("Image payload is invalid") from exc
    finally:
        file_data.seek(0)
    return _ALLOWED_FORMATS[detected_format]
