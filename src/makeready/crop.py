"""Image crops that put a focal point on the same point of the frame."""

from dataclasses import dataclass

_FITS = {"fill", "fit", "none"}


@dataclass(frozen=True)
class Crop:
    scale: float
    x: float
    y: float


def crop(
    frame_w: float,
    frame_h: float,
    img_w: float,
    img_h: float,
    fit: str = "fill",
    focus: tuple[float, float] = (0.5, 0.5),
) -> Crop:
    if fit not in _FITS:
        raise ValueError(f"unknown fit {fit!r}")
    fx, fy = focus
    if fx < 0 or fx > 1 or fy < 0 or fy > 1:
        raise ValueError(f"focus {focus!r} is outside 0 to 1")
    if frame_w <= 0 or frame_h <= 0 or img_w <= 0 or img_h <= 0:
        raise ValueError("frame and image sizes must be positive")
    if fit == "fill":
        scale = max(frame_w / img_w, frame_h / img_h)
    elif fit == "fit":
        scale = min(frame_w / img_w, frame_h / img_h)
    else:
        scale = 1.0
    return Crop(
        scale=float(scale),
        x=float(fx * (frame_w - img_w * scale)),
        y=float(fy * (frame_h - img_h * scale)),
    )
