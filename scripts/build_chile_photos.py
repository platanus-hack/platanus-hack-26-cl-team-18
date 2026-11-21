"""Build the Chile catalog's campaign photos (DEMO_CATALOG=chile). Cold, local, reproducible.

Two sources, never a generated logo or a generated real product (CLAUDE.md invariant 4):

1. Real Chilean counter food from Wikimedia Commons (completo italiano, café con
   leche with a pastry), downloaded, cropped to 16:9 and credited in
   assets/campaigns/photos/SOURCES.md as their licenses require.
2. The bundled unbranded studio shots, relit per brand: the rim light on the blank can
   is moved to the brand's hue (yellow for Score, red for Red Bull), so a rival never
   wears another brand's color. No mark, label or packaging is drawn.

    uv run python -m scripts.build_chile_photos            # build everything
    uv run python -m scripts.build_chile_photos --skip-download
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

ROOT = Path(__file__).resolve().parents[1] / "assets" / "campaigns" / "photos"
SIZE = (1600, 900)
USER_AGENT = "advisor-demo-asset-build/0.1 (local hackathon demo)"


@dataclass(frozen=True, slots=True)
class CommonsPhoto:
    stem: str
    title: str
    # Crop centre as fractions of width/height, then extra zoom (1 = widest 16:9 crop).
    focus: tuple[float, float] = (0.5, 0.5)
    zoom: float = 1.0
    # Put the subject on the right so the ad's copy has the left (only photos without text).
    mirror: bool = False


@dataclass(frozen=True, slots=True)
class Relight:
    stem: str
    base: str
    # PIL HSV hue (0-255) window to move, and the brand hue it moves to.
    hue_from: tuple[int, int]
    hue_to: int
    saturation: float = 1.0
    zoom: float = 1.0
    focus: tuple[float, float] = (0.5, 0.5)


COMMONS: tuple[CommonsPhoto, ...] = (
    CommonsPhoto(
        stem="cl-completo",
        title="File:Completo italiano.jpg",
        focus=(0.5, 0.52),
        mirror=True,
    ),
    # Top-aligned: the 4:3 original loses height at 16:9; keep the cup's rim.
    CommonsPhoto(
        stem="cl-cafe-medialuna", title="File:Taza de café con leche.jpg", focus=(0.5, 0.0)
    ),
    # Rotation and category scenes (manifest `alternates`, `category:<name>`): more of
    # the same moment so a returning viewer sees a new frame. Food, drink and road only:
    # no faces, no third-party marks.
    CommonsPhoto(
        stem="cl-completo-palta",
        title="File:Completo italiano - Flickr - Bárb Santelices.jpg",
        focus=(0.5, 0.5),
    ),
    CommonsPhoto(
        stem="cl-desayuno",
        title="File:Coffee-morning-breakfast-croissant (24300690486).jpg",
        focus=(0.5, 0.5),
    ),
    CommonsPhoto(
        stem="cl-cappuccino",
        title="File:Cup of frothy coffee, Cafe Express, York Way, London, England.jpg",
        focus=(0.5, 0.5),
    ),
    CommonsPhoto(
        stem="cl-bebida-noche",
        title="File:DSC 7821 A tall glass of cola with ice and a white straw sits on a table softly lit against a blurred evening backdrop of warm bokeh lights.jpg",
        focus=(0.5, 0.5),
    ),
    CommonsPhoto(stem="cl-bebida-limon", title="File:Glass cola.jpg", focus=(0.5, 0.5)),
    CommonsPhoto(
        stem="cl-helado-playa", title="File:2 ice cream cones 2025-05-02.jpg", focus=(0.5, 0.45)
    ),
    CommonsPhoto(
        stem="cl-paleta", title="File:Patriotic Ice Pop (4685807904).jpg", focus=(0.5, 0.35)
    ),
    CommonsPhoto(stem="cl-chocolatada", title="File:Chocolate milk.JPG", focus=(0.55, 0.5)),
    CommonsPhoto(stem="cl-nectar", title="File:Orange juice 1 edit1.jpg", focus=(0.5, 0.62)),
    CommonsPhoto(stem="cl-papas", title="File:Papas fritas bowl.jpg", focus=(0.5, 0.5)),
    CommonsPhoto(stem="cl-papas-rusticas", title="File:Dill pickle chips.jpg", focus=(0.5, 0.5)),
    CommonsPhoto(
        stem="cl-ruta-amanecer", title="File:Ruta 5 Sur Chile (km 104,8).jpg", focus=(0.5, 0.6)
    ),
    CommonsPhoto(stem="cl-ruta-5", title="File:Ruta 5 Sur (Chile) en Pelequén.jpg"),
    CommonsPhoto(stem="cl-agua", title="File:Bottles (4782930999).jpg"),
    CommonsPhoto(stem="cl-chocolate", title="File:Opened Tupla chocolate bar.jpg"),
    CommonsPhoto(stem="cl-dulces", title="File:HardCandy.jpg"),
    # The venue itself: a real Copec station in La Reina (Pronto Copec's own campaigns).
    CommonsPhoto(stem="cl-copec-estacion", title="File:Copec La Reina.jpg", focus=(0.5, 0.45)),
    CommonsPhoto(
        stem="cl-helado-bombon",
        title="File:Liat Portal for Foodie Disorder - Chocolate coated ice cream bar with nuts.jpg",
        focus=(0.5, 0.4),
    ),
)

# PIL hue: lime ≈ 50-75, violet ≈ 170-200, yellow ≈ 30-34, blue ≈ 150-160.
# Red Bull goes blue (its blue-and-silver can), not its red accent: a pastel can
# shifted to red reads pink.
RELIGHTS: tuple[Relight, ...] = (
    Relight(stem="cl-score", base="energy", hue_from=(45, 80), hue_to=32, saturation=1.2),
    Relight(stem="cl-red-bull", base="rayo", hue_from=(160, 215), hue_to=152, saturation=1.25),
    Relight(stem="cl-monster", base="energy", hue_from=(45, 80), hue_to=62, saturation=1.2),
    Relight(
        stem="cl-score-cafe", base="energy-coffee", hue_from=(45, 80), hue_to=30, saturation=1.2
    ),
    Relight(
        stem="cl-red-bull-cafe",
        base="rayo-coffee",
        hue_from=(160, 215),
        hue_to=152,
        saturation=1.3,
    ),
)


def _api(params: dict[str, str]) -> dict:
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
        {**params, "format": "json"}
    )
    return json.load(_get(url))


def _get(url: str, *, attempts: int = 5) -> io.BytesIO:
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=90) as response:
                return io.BytesIO(response.read())
        except Exception:  # rate limits and flaky networks: back off, retry
            if attempt == attempts - 1:
                raise
            time.sleep(30 * (attempt + 1))
    raise AssertionError("unreachable")


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()


def _frame(image: Image.Image, focus: tuple[float, float], zoom: float) -> Image.Image:
    """Crop to 16:9 around `focus`, zoomed in by `zoom`, and resize to SIZE."""
    width, height = image.size
    target = SIZE[0] / SIZE[1]
    crop_w, crop_h = (
        (width, width / target) if width / height < target else (height * target, height)
    )
    crop_w, crop_h = crop_w / zoom, crop_h / zoom
    left = min(max(focus[0] * width - crop_w / 2, 0), width - crop_w)
    top = min(max(focus[1] * height - crop_h / 2, 0), height - crop_h)
    box = (round(left), round(top), round(left + crop_w), round(top + crop_h))
    return image.crop(box).resize(SIZE, Image.Resampling.LANCZOS)


def _relight(image: Image.Image, spec: Relight) -> Image.Image:
    hue, sat, val = image.convert("HSV").split()
    low, high = spec.hue_from
    inside = hue.point(lambda h: 255 if low <= h <= high else 0)
    colorful = sat.point(lambda s: 255 if s >= 40 else int(s * 255 / 40))
    mask = ImageChops.multiply(inside, colorful)
    moved = Image.merge(
        "HSV",
        (
            hue.point(lambda _h: spec.hue_to),
            sat.point(lambda s: min(255, round(s * spec.saturation))),
            val,
        ),
    ).convert("RGB")
    return Image.composite(moved, image.convert("RGB"), mask)


def _save(image: Image.Image, stem: str) -> None:
    path = ROOT / f"{stem}.webp"
    image.save(path, "WEBP", quality=84, method=6)
    print(f"photos/{path.name}: {path.stat().st_size // 1024} KiB")


def _download_and_save(photo: CommonsPhoto, meta: dict, originals: dict[str, Image.Image]) -> None:
    if photo.title not in originals:
        time.sleep(12)
        try:
            data = _get(meta.get("thumburl", meta["url"]), attempts=1)
        except Exception:
            # Thumbnail renders are rate-limited per IP (429); the cached original is not.
            data = _get(meta["url"])
        original = ImageOps.exif_transpose(Image.open(data)).convert("RGB")
        original.thumbnail((3200, 3200))  # originals can be 8000+ px wide
        originals[photo.title] = original
    framed = _frame(originals[photo.title], photo.focus, photo.zoom)
    _save(ImageOps.mirror(framed) if photo.mirror else framed, photo.stem)


def build_commons(credits: list[str], *, force: bool = False) -> None:
    titles = sorted({photo.title for photo in COMMONS})
    pages = _api(
        {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            # A cached 1920 px rendition (a standard Commons width): enough for 1600x900, far gentler on the
            # rate limit than full-size originals.
            "iiurlwidth": "1920",
        }
    )["query"]["pages"].values()
    info = {page["title"]: page["imageinfo"][0] for page in pages}
    originals: dict[str, Image.Image] = {}
    for photo in COMMONS:
        meta = info[photo.title]
        # Resumable: Commons rate-limits bulk downloads (429), so a rerun only fetches
        # what is missing. --force rebuilds everything.
        if force or not (ROOT / f"{photo.stem}.webp").is_file():
            _download_and_save(photo, meta, originals)
        ext = meta.get("extmetadata", {})
        artist = _strip_html(ext.get("Artist", {}).get("value", "unknown"))
        license_name = ext.get("LicenseShortName", {}).get("value", "see source")
        credits.append(
            f"| `{photo.stem}.webp` | [{photo.title[5:]}]({meta['descriptionurl']}) "
            f"| {artist} | {license_name} | cropped to 16:9"
            + (", mirrored" if photo.mirror else "")
            + " |"
        )


def build_relights() -> None:
    for spec in RELIGHTS:
        base = Image.open(ROOT / f"{spec.base}.webp").convert("RGB")
        _save(_frame(_relight(base, spec), spec.focus, spec.zoom), spec.stem)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-download", action="store_true", help="Only relight local shots")
    parser.add_argument("--force", action="store_true", help="Re-download photos already built")
    args = parser.parse_args()
    build_relights()
    if args.skip_download:
        return
    credits: list[str] = []
    build_commons(credits, force=args.force)
    (ROOT / "SOURCES.md").write_text(
        "# Photo sources\n\n"
        "Fictional-catalog photos (`energy`, `coffee`, `beer`, `rayo`, `energy-coffee`,\n"
        "`rayo-coffee`) are unbranded AI scenery; prompts in `../prompts.json`.\n\n"
        "Chile catalog (`DEMO_CATALOG=chile`), built by `scripts/build_chile_photos.py`:\n\n"
        "- `cl-score`, `cl-red-bull`, `cl-monster`, `cl-score-cafe`, `cl-red-bull-cafe`: the\n"
        "  unbranded studio shots above with the can's rim light moved to the brand's hue.\n"
        "  No mark, label or packaging is drawn (CLAUDE.md invariant 4).\n"
        "- Real photos from Wikimedia Commons (Chilean counter food, drinks and sweets,\n"
        "  Ruta 5 and a Copec station), credited below as the licenses require. Food,\n"
        "  drink and road only: no recognisable faces, no third-party marks. CC BY-SA\n"
        "  derivatives (the crops) stay under CC BY-SA. They air in rotation and as\n"
        "  category scenes (manifest `alternates`, `category:<name>`).\n\n"
        "| File | Source | Author | License | Changes |\n|---|---|---|---|---|\n"
        + "\n".join(credits)
        + "\n"
    )
    print("photos/SOURCES.md")


if __name__ == "__main__":
    main()
