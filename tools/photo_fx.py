"""Спецэффекты для кадров «Зарубы»: неон, световые ленты, частицы, зерно.

Нейросети здесь нет и не будет: скрипт не дорисовывает людей и не меняет лица.
Всё, что он делает, — детерминированная графика на Pillow: цветокоррекция,
свечение, виньетка, шум. Один и тот же slug всегда даёт один и тот же постер,
поэтому результат можно закоммитить и не пересобирать при каждом запуске.

Два режима:

* `apply_dance_fx(image)` — эффекты поверх **настоящего** снимка. Лицо остаётся
  собой: меняются только свет, цвет и зерно.
* `poster(slug)` — иллюстрация с силуэтом танцора. Это не фотография и не
  портрет: на сайте она подписана «иллюстрация», а не «фото».
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import TYPE_CHECKING

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

if TYPE_CHECKING:  # pragma: no cover - только для типов
    from collections.abc import Callable, Sequence

RGB = tuple[int, int, int]
POINT = tuple[float, float]

# Палитра проекта: лайм из `style.css` плюс два неона, которые читаются на тёмном.
LIME: RGB = (232, 255, 58)
CYAN: RGB = (86, 214, 255)
MAGENTA: RGB = (255, 92, 150)
CORAL: RGB = (255, 92, 56)
NEONS: tuple[RGB, ...] = (LIME, CYAN, MAGENTA, CORAL)


def _rng(slug: str) -> random.Random:
    """Стабильный поток случайных чисел: slug — это и есть seed."""

    digest = hashlib.sha256(slug.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _vertical_gradient(size: tuple[int, int], top: RGB, bottom: RGB) -> Image.Image:
    width, height = size
    strip = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        pixel = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        strip.putpixel((0, y), pixel)  # type: ignore[arg-type]
    return strip.resize((width, height), Image.BILINEAR)


def _radial(size: tuple[int, int], center: POINT, radius: float) -> Image.Image:
    """Белая точка с мягким краем на чёрном: маска для свечения."""

    scale = 4
    small = (max(size[0] // scale, 8), max(size[1] // scale, 8))
    mask = Image.new("L", small, 0)
    draw = ImageDraw.Draw(mask)
    steps = 24
    for step in range(steps, 0, -1):
        t = step / steps
        r = radius / scale * t
        value = round(255 * (1 - t) ** 2)
        draw.ellipse(
            (
                center[0] / scale - r,
                center[1] / scale - r,
                center[0] / scale + r,
                center[1] / scale + r,
            ),
            fill=value,
        )
    return mask.resize(size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(radius / 6))


def _screen(base: Image.Image, layer: Image.Image, mask: Image.Image | None = None) -> None:
    """Аддитивное наложение на месте: свет складывается, тени не мутнеют."""

    if mask is not None:
        layer = Image.composite(layer, Image.new("RGB", base.size, (0, 0, 0)), mask)
    base.paste(ImageChops.screen(base, layer))


def _screen_tint(base: Image.Image, color: RGB, mask: Image.Image, amount: float = 1.0) -> None:
    tinted = tuple(round(channel * amount) for channel in color)
    _screen(base, Image.new("RGB", base.size, tinted), mask)


def _vignette(size: tuple[int, int], strength: float = 0.72) -> Image.Image:
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    steps = 40
    for step in range(steps):
        t = step / steps
        inset_x = width * 0.5 * t
        inset_y = height * 0.5 * t
        value = int(255 * (1 - strength * (1 - t) ** 2))
        draw.rectangle((inset_x, inset_y, width - inset_x, height - inset_y), fill=value)
    return mask.filter(ImageFilter.GaussianBlur(width / 12))


def _grain(
    image: Image.Image, rng: random.Random, *, sigma: float = 9.0, alpha: float = 0.06
) -> Image.Image:
    """Зерно из seeded-генератора: `Image.effect_noise` даёт разный файл при каждом запуске."""

    width, height = image.size
    noise = Image.frombytes("L", (width, height), rng.randbytes(width * height))
    noise = noise.point(lambda value: min(255, max(0, round(128 + (value - 128) * sigma / 40))))
    return Image.blend(image, ImageChops.overlay(image, noise.convert("RGB")), alpha)


def _ribbons(
    size: tuple[int, int],
    rng: random.Random,
    count: int,
    palette: Sequence[RGB],
    *,
    width_scale: float,
) -> Image.Image:
    """Световые ленты: кривые, которые рисует концертный свет, а не фотошоп."""

    width, height = size
    layer = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        color = rng.choice(palette)
        x = rng.uniform(-0.15, 1.0) * width
        y = rng.uniform(-0.05, 1.05) * height
        angle = rng.uniform(-math.pi / 2, math.pi / 2)
        points: list[POINT] = [(x, y)]
        for _ in range(rng.randint(3, 6)):
            angle += rng.uniform(-0.55, 0.55)
            step = rng.uniform(0.10, 0.22) * width
            x += math.cos(angle) * step
            y += math.sin(angle) * step
            points.append((x, y))
        _stroke(draw, points, color, max(1, round(rng.uniform(2.0, 6.0) * width_scale)))
    return layer.filter(ImageFilter.GaussianBlur(max(1.0, width / 160)))


def _particles(
    size: tuple[int, int],
    rng: random.Random,
    count: int,
    palette: Sequence[RGB],
    *,
    width_scale: float,
) -> Image.Image:
    """Пыль в луче света: мелкие точки дают объём, не превращаясь в «снег»."""

    width, height = size
    layer = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        x = rng.uniform(0, width)
        y = rng.uniform(0, height)
        radius = rng.uniform(0.5, 2.2) * width_scale
        color = rng.choice(palette) if rng.random() < 0.65 else (255, 255, 255)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    return layer.filter(ImageFilter.GaussianBlur(max(0.8, width_scale)))


def _stroke(draw: ImageDraw.ImageDraw, points: Sequence[POINT], color: object, width: int) -> None:
    """Линия с круглыми концами: без них конечности выглядят обрубленными."""

    draw.line(points, fill=color, width=width, joint="curve")
    radius = width / 2
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def _capsule(
    draw: ImageDraw.ImageDraw,
    start: POINT,
    end: POINT,
    width_start: float,
    width_end: float,
    color: object,
) -> None:
    """Сегмент с разной толщиной на концах: бедро толще голени, плечо — кисти."""

    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    normal_x, normal_y = -dy / length, dx / length
    corners = [
        (start[0] + normal_x * width_start / 2, start[1] + normal_y * width_start / 2),
        (end[0] + normal_x * width_end / 2, end[1] + normal_y * width_end / 2),
        (end[0] - normal_x * width_end / 2, end[1] - normal_y * width_end / 2),
        (start[0] - normal_x * width_start / 2, start[1] - normal_y * width_start / 2),
    ]
    draw.polygon(corners, fill=color)
    for (x, y), width in ((start, width_start), (end, width_end)):
        radius = width / 2
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def _dot(draw: ImageDraw.ImageDraw, center: POINT, rx: float, ry: float, color: object) -> None:
    draw.ellipse((center[0] - rx, center[1] - ry, center[0] + rx, center[1] + ry), fill=color)


def _beams(
    size: tuple[int, int], rng: random.Random, count: int, palette: Sequence[RGB]
) -> Image.Image:
    """Прожекторы сверху: конусы света, которые дают «сцену», а не «фон»."""

    width, height = size
    layer = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        origin_x = rng.uniform(0.05, 0.95) * width
        spread = rng.uniform(0.08, 0.20) * width
        end_x = origin_x + rng.uniform(-0.3, 0.3) * width
        draw.polygon(
            [
                (origin_x - 0.01 * width, -0.05 * height),
                (origin_x + 0.01 * width, -0.05 * height),
                (end_x + spread, height * 1.05),
                (end_x - spread, height * 1.05),
            ],
            fill=rng.choice(palette),
        )
    return layer.filter(ImageFilter.GaussianBlur(width / 40))


def _light_streaks(
    size: tuple[int, int], rng: random.Random, count: int, palette: Sequence[RGB]
) -> Image.Image:
    """Следы света: длинные дуги с утолщением в середине, как на длинной выдержке."""

    width, height = size
    layer = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        color = rng.choice(palette)
        start = (rng.uniform(-0.25, 0.45) * width, rng.uniform(-0.1, 1.0) * height)
        end = (rng.uniform(0.60, 1.25) * width, rng.uniform(-0.1, 1.0) * height)
        control = (
            (start[0] + end[0]) / 2 + rng.uniform(-0.28, 0.28) * width,
            (start[1] + end[1]) / 2 + rng.uniform(-0.38, 0.38) * height,
        )
        steps = 40
        previous: POINT | None = None
        previous_radius = 0.0
        for step in range(steps):
            t = step / (steps - 1)
            x = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t**2 * end[0]
            y = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t**2 * end[1]
            radius = (0.002 + 0.020 * math.sin(math.pi * t)) * width
            if previous is not None:
                _capsule(draw, previous, (x, y), previous_radius * 2, radius * 2, color)
            previous, previous_radius = (x, y), radius
    return layer.filter(ImageFilter.GaussianBlur(width / 70))


def _haze(size: tuple[int, int], rng: random.Random, palette: Sequence[RGB]) -> Image.Image:
    """Дым у пола: сцена без него выглядит как коллаж, а не как зал."""

    width, height = size
    layer = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(6):
        x = rng.uniform(0, width)
        y = rng.uniform(0.35, 1.0) * height
        radius_x = rng.uniform(0.15, 0.35) * width
        radius_y = rng.uniform(0.05, 0.12) * height
        draw.ellipse(
            (x - radius_x, y - radius_y, x + radius_x, y + radius_y),
            fill=rng.choice(palette),
        )
    return layer.filter(ImageFilter.GaussianBlur(width / 25))


def _crowd(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """Размытые головы первого ряда: тёмный передний план, чтобы был зал."""

    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for _ in range(rng.randint(4, 7)):
        x = rng.uniform(-0.1, 1.1) * width
        y = height * rng.uniform(1.02, 1.14)
        radius = rng.uniform(0.06, 0.12) * width
        draw.ellipse((x - radius, y - radius * 0.9, x + radius, y + radius * 0.9), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(width / 40))


def _pose(rng: random.Random) -> dict[str, POINT]:
    """Поза в долях кадра: прыжок, наклон или выпад — по seed.

    Стоящий «по стойке» силуэт всегда читается как манекен: у процедурной
    фигуры не хватает деталей. Динамичная поза это скрывает и работает на
    тему чемпионата.
    """

    lean = rng.uniform(-0.05, 0.05)
    tilt = rng.uniform(-0.04, 0.04)

    def point(x: float, y: float) -> POINT:
        return (x + lean + tilt * (0.45 - y), y)

    def jitter(value: float, spread: float) -> float:
        return value + rng.uniform(-spread, spread)

    variants: dict[str, dict[str, POINT]] = {
        "jump": {
            "head": point(0.500, 0.150),
            "neck": point(0.500, 0.208),
            "chest": point(0.500, 0.278),
            "waist": point(0.500, 0.356),
            "hip": point(0.500, 0.416),
            "hip_l": point(0.462, 0.416),
            "hip_r": point(0.538, 0.416),
            "shoulder_l": point(0.416, 0.246),
            "shoulder_r": point(0.584, 0.246),
            "elbow_l": point(jitter(0.322, 0.014), jitter(0.172, 0.014)),
            "hand_l": point(jitter(0.248, 0.018), jitter(0.112, 0.018)),
            "elbow_r": point(jitter(0.700, 0.014), jitter(0.292, 0.014)),
            "hand_r": point(jitter(0.776, 0.018), jitter(0.238, 0.018)),
            "knee_l": point(jitter(0.386, 0.012), jitter(0.558, 0.014)),
            "foot_l": point(jitter(0.336, 0.014), jitter(0.678, 0.016)),
            "knee_r": point(jitter(0.618, 0.012), jitter(0.544, 0.014)),
            "foot_r": point(jitter(0.688, 0.014), jitter(0.652, 0.016)),
        },
        "lean": {
            "head": point(0.462, 0.166),
            "neck": point(0.476, 0.226),
            "chest": point(0.490, 0.300),
            "waist": point(0.505, 0.394),
            "hip": point(0.516, 0.470),
            "hip_l": point(0.478, 0.470),
            "hip_r": point(0.556, 0.470),
            "shoulder_l": point(0.406, 0.256),
            "shoulder_r": point(0.576, 0.246),
            "elbow_l": point(jitter(0.318, 0.014), jitter(0.196, 0.014)),
            "hand_l": point(jitter(0.246, 0.018), jitter(0.132, 0.018)),
            "elbow_r": point(jitter(0.700, 0.014), jitter(0.322, 0.014)),
            "hand_r": point(jitter(0.802, 0.018), jitter(0.398, 0.018)),
            "knee_l": point(jitter(0.444, 0.012), jitter(0.688, 0.014)),
            "foot_l": point(jitter(0.402, 0.014), jitter(0.906, 0.010)),
            "knee_r": point(jitter(0.598, 0.012), jitter(0.690, 0.014)),
            "foot_r": point(jitter(0.676, 0.014), jitter(0.884, 0.012)),
        },
        "lunge": {
            "head": point(0.486, 0.170),
            "neck": point(0.488, 0.230),
            "chest": point(0.492, 0.304),
            "waist": point(0.496, 0.398),
            "hip": point(0.500, 0.474),
            "hip_l": point(0.462, 0.474),
            "hip_r": point(0.538, 0.474),
            "shoulder_l": point(0.408, 0.260),
            "shoulder_r": point(0.572, 0.252),
            "elbow_l": point(jitter(0.300, 0.014), jitter(0.344, 0.014)),
            "hand_l": point(jitter(0.230, 0.018), jitter(0.438, 0.018)),
            "elbow_r": point(jitter(0.664, 0.014), jitter(0.186, 0.014)),
            "hand_r": point(jitter(0.742, 0.018), jitter(0.104, 0.018)),
            "knee_l": point(jitter(0.338, 0.016), jitter(0.690, 0.014)),
            "foot_l": point(jitter(0.258, 0.018), jitter(0.902, 0.010)),
            "knee_r": point(jitter(0.612, 0.012), jitter(0.660, 0.014)),
            "foot_r": point(jitter(0.716, 0.014), jitter(0.898, 0.010)),
        },
    }
    return variants[rng.choice(sorted(variants))]


def _paint_figure(
    draw: ImageDraw.ImageDraw, size: tuple[int, int], pose: dict[str, POINT], color: object
) -> None:
    """Силуэт без лица и деталей: поза читается, личность — нет.

    Толщина сегментов разная (бедро толще голени, плечо — кисти), иначе
    фигура выглядит палочной схемой, а не человеком в движении.
    """

    width, height = size
    unit = float(width)

    def xy(point: POINT) -> POINT:
        return (point[0] * width, point[1] * height)

    for hip, knee, foot, side in (
        ("hip_l", "knee_l", "foot_l", -1.0),
        ("hip_r", "knee_r", "foot_r", 1.0),
    ):
        _capsule(draw, xy(pose[hip]), xy(pose[knee]), 0.070 * unit, 0.046 * unit, color)
        _capsule(draw, xy(pose[knee]), xy(pose[foot]), 0.046 * unit, 0.030 * unit, color)
        foot_x, foot_y = xy(pose[foot])
        _dot(draw, (foot_x + side * 0.014 * unit, foot_y), 0.032 * unit, 0.011 * unit, color)

    _capsule(draw, xy(pose["hip"]), xy(pose["waist"]), 0.150 * unit, 0.112 * unit, color)
    _capsule(draw, xy(pose["waist"]), xy(pose["chest"]), 0.112 * unit, 0.165 * unit, color)
    _capsule(draw, xy(pose["chest"]), xy(pose["neck"]), 0.165 * unit, 0.080 * unit, color)
    _capsule(
        draw, xy(pose["shoulder_l"]), xy(pose["shoulder_r"]), 0.062 * unit, 0.062 * unit, color
    )
    for shoulder, elbow, hand in (
        ("shoulder_l", "elbow_l", "hand_l"),
        ("shoulder_r", "elbow_r", "hand_r"),
    ):
        _capsule(draw, xy(pose[shoulder]), xy(pose[elbow]), 0.052 * unit, 0.038 * unit, color)
        _capsule(draw, xy(pose[elbow]), xy(pose[hand]), 0.038 * unit, 0.023 * unit, color)
        _dot(draw, xy(pose[hand]), 0.022 * unit, 0.030 * unit, color)
    _capsule(draw, xy(pose["neck"]), xy(pose["head"]), 0.050 * unit, 0.046 * unit, color)
    _dot(draw, xy(pose["head"]), 0.045 * unit, 0.056 * unit, color)


def _trails(mask: Image.Image, accent: RGB, rng: random.Random, *, amount: int = 5) -> Image.Image:
    """Шлейф движения: несколько размытых копий силуэта под разными углами.

    Длинная выдержка прощает геометрию: поза читается как движение, а не как
    схема человека. Слой кладётся под основную фигуру.
    """

    width, height = mask.size
    layer = Image.new("RGB", mask.size, (0, 0, 0))
    center = (width * 0.5, height * 0.55)
    for index in range(amount):
        angle = rng.uniform(-10.0, 10.0) * (index + 1) * 0.6
        ghost = mask.rotate(angle, resample=Image.BICUBIC, center=center)
        ghost = ghost.filter(ImageFilter.GaussianBlur(width / 120 + index * 2))
        tint = Image.new("RGB", mask.size, accent).point(_dim_by(0.28 - index * 0.035))
        layer.paste(tint, (0, 0), ghost)
    return layer


def _dim_by(factor: float) -> Callable[[int], int]:
    """Готовый аргумент для `Image.point`: приглушить слой, не трогая размер."""

    def apply(value: int | float) -> int:
        return round(value * factor)

    return apply


def poster(slug: str, size: tuple[int, int]) -> Image.Image:
    """Иллюстрация «танцор в неоне»: сцена, силуэт, свет, пыль. Не фотография.

    Рисуется в двойном разрешении и уменьшается — так края остаются гладкими.
    Slug задаёт и позу, и палитру: два запуска дают одинаковый файл.
    """

    scale = 2
    canvas = (size[0] * scale, size[1] * scale)
    width, height = canvas
    rng = _rng(slug)
    accent = rng.choice(NEONS)

    base = _vertical_gradient(canvas, (10, 12, 24), (23, 19, 34))
    _screen(base, _beams(canvas, rng, 3, NEONS).point(_dim_by(0.14)))
    _screen(base, _light_streaks(canvas, rng, 2, NEONS).point(_dim_by(0.28)))
    _screen_tint(base, accent, _radial(canvas, (width * 0.5, height * 0.36), width * 0.70), 0.24)
    _screen_tint(
        base,
        rng.choice(NEONS),
        _radial(
            canvas,
            (width * rng.uniform(0.12, 0.88), height * rng.uniform(0.10, 0.50)),
            width * 0.40,
        ),
        0.20,
    )
    _screen(base, _haze(canvas, rng, NEONS).point(_dim_by(0.10)))

    pose = _pose(rng)
    figure = Image.new("L", canvas, 0)
    _paint_figure(ImageDraw.Draw(figure), canvas, pose, 255)
    # Смаз движения: резкий силуэт читается как схема человека, смазанный —
    # как кадр с длинной выдержкой. Плюс фигура в 3/4 кадра, а не в полный рост.
    steps = 10
    smeared = Image.new("L", canvas, 0)
    for index in range(steps):
        ghost = ImageChops.offset(figure, int(index * 0.008 * width), int(index * 0.002 * height))
        smeared = ImageChops.lighter(smeared, ghost.point(_dim_by(1 - index / (steps * 1.6))))
    shrink = 0.78
    silhouette = Image.new("L", canvas, 0)
    silhouette.paste(
        smeared.resize((int(width * shrink), int(height * shrink)), Image.LANCZOS),
        (
            int(width * (1 - shrink) / 2),
            int(height * 0.925 * (1 - shrink)),
        ),
    )
    _screen(base, _trails(silhouette, accent, rng))
    # Сначала свечение вокруг фигуры, потом тёмное тело, потом светлый контур:
    # так силуэт отделяется от фона, не превращаясь в плоскую наклейку.
    aura = Image.new("RGB", canvas, (0, 0, 0))
    aura.paste(Image.new("RGB", canvas, accent), mask=silhouette)
    _screen(base, aura.filter(ImageFilter.GaussianBlur(width / 48)).point(_dim_by(0.55)))
    base.paste(
        _vertical_gradient(canvas, (18, 18, 27), (6, 6, 10)),
        mask=silhouette.filter(ImageFilter.GaussianBlur(scale * 1.6)),
    )
    edge = ImageChops.subtract(silhouette, silhouette.filter(ImageFilter.GaussianBlur(2.4 * scale)))
    rim = Image.new("RGB", canvas, (0, 0, 0))
    rim.paste(
        Image.new("RGB", canvas, accent), mask=edge.filter(ImageFilter.GaussianBlur(scale * 1.2))
    )
    _screen(base, rim)

    # Пол и отражение: без них фигура «висит» в воздухе.
    floor_y = int(height * 0.925)
    strip = max(height - floor_y, 1)
    mirror = silhouette.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    mirror = mirror.resize((width, strip * 2)).crop((0, 0, width, strip))
    mirror = mirror.filter(ImageFilter.GaussianBlur(width / 90)).point(_dim_by(0.5))
    reflection = Image.new("RGB", canvas, (0, 0, 0))
    reflection.paste(Image.new("RGB", (width, strip), accent), (0, floor_y), mirror)
    _screen(base, reflection)
    floor = Image.new("RGB", canvas, (0, 0, 0))
    ImageDraw.Draw(floor).rectangle((0, floor_y, width, floor_y + max(2, scale * 2)), fill=accent)
    _screen(base, floor.filter(ImageFilter.GaussianBlur(width / 300)).point(_dim_by(0.18)))

    _screen(base, _particles(canvas, rng, 45, NEONS, width_scale=scale).point(_dim_by(0.6)))
    base.paste(Image.new("RGB", canvas, (4, 4, 7)), mask=_crowd(canvas, rng))

    base = Image.composite(base, Image.new("RGB", canvas, (0, 0, 0)), _vignette(canvas, 0.72))
    base = _grain(base, rng, sigma=10, alpha=0.06)
    return base.resize(size, Image.LANCZOS)


def apply_dance_fx(
    image: Image.Image, *, seed: str = "dance", strength: float = 1.0
) -> Image.Image:
    """Эффекты поверх настоящего снимка: цвет, свет, пыль, виньетка, зерно.

    Лицо не трогаем: ни морфинга, ни «улучшения» черт. Только свет и цвет —
    то, что фотограф сделал бы в редакторе, а не то, что придумала нейросеть.
    """

    base = image.convert("RGB")
    size = base.size
    scale = max(size[0] / 480, 1.0)
    rng = _rng(seed)

    base = ImageEnhance.Color(base).enhance(1.0 + 0.12 * strength)
    base = ImageEnhance.Contrast(base).enhance(1.0 + 0.06 * strength)
    duotone = ImageOps.colorize(ImageOps.grayscale(base), black=(16, 12, 34), white=LIME, mid=CYAN)
    base = Image.blend(base, duotone, 0.28 * strength)

    _screen_tint(
        base,
        CYAN,
        _radial(size, (size[0] * 0.25, size[1] * 0.18), size[0] * 0.55),
        0.45 * strength,
    )
    _screen_tint(
        base,
        MAGENTA,
        _radial(size, (size[0] * 0.82, size[1] * 0.85), size[0] * 0.45),
        0.30 * strength,
    )
    ribbons = _ribbons(size, rng, 4, NEONS, width_scale=scale).point(_dim_by(0.45))
    _screen(base, ribbons)
    _screen(base, _particles(size, rng, 110, NEONS, width_scale=scale))

    base = Image.composite(base, Image.new("RGB", size, (0, 0, 0)), _vignette(size, 0.55))
    return _grain(base, rng, sigma=7, alpha=0.05 * strength)
