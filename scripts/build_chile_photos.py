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

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

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
    # A brand's own product photo (a real can or bottle, its real label): shown whole at
    # screen height on the right, over a blurred, darkened copy of the same photo, so a
    # portrait pack shot is never cropped mid-label and the copy keeps the left.
    pack: bool = False
    # Fractions (left, top, right, bottom) to cut from the original first: a group of
    # products inside a wider shot (six cans of a stacked lineup). Never retouched.
    region: tuple[float, float, float, float] | None = None


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
    CommonsPhoto(stem="cl-chocolatada", title="File:Chocolate milk.JPG", focus=(0.55, 0.5)),
    CommonsPhoto(stem="cl-nectar", title="File:Orange juice 1 edit1.jpg", pack=True),
    # Medialunas with coffee: a Commons "Quality image" (sharp, well lit).
    CommonsPhoto(stem="cl-medialunas", title="File:MediasLunasenMardelPlata.jpg", mirror=True),
    CommonsPhoto(stem="cl-papas-rusticas", title="File:Dill pickle chips.jpg", focus=(0.5, 0.5)),
    CommonsPhoto(
        stem="cl-ruta-amanecer", title="File:Ruta 5 Sur Chile (km 104,8).jpg", focus=(0.5, 0.6)
    ),
    CommonsPhoto(stem="cl-ruta-5", title="File:Ruta 5 Sur (Chile) en Pelequén.jpg"),
    CommonsPhoto(stem="cl-agua", title="File:Bottles (4782930999).jpg"),
    CommonsPhoto(stem="cl-chocolate", title="File:Opened Tupla chocolate bar.jpg"),
    CommonsPhoto(stem="cl-dulces", title="File:HardCandy.jpg"),
    # The brands' own products, real labels (pack shots): a brand's campaign shows its
    # product, never a rival's; category scenes stay unbranded.
    CommonsPhoto(
        stem="cl-monster-mega",
        title="File:Monster Energy Mega can.jpg",
        pack=True,
    ),
    CommonsPhoto(stem="cl-monster-lineup", title="File:Monster Energy drinks 04.jpg"),
    # The hackathon promo's six-pack: six whole cans (3 x 2) from a stacked lineup.
    CommonsPhoto(
        stem="cl-monster-sixpack",
        title="File:Monster Energy drinks 11.jpg",
        pack=True,
        region=(0.561, 0.015, 0.962, 0.905),
    ),
    CommonsPhoto(
        stem="cl-coca-cola-zero", title="File:Coca Cola Zero 1L Sense Sucre Cara A.jpg", pack=True
    ),
    CommonsPhoto(stem="cl-pepsi-max", title="File:Pepsi Max can.jpg", pack=True),
    CommonsPhoto(stem="cl-bilz", title="File:Botella Bilz 2023.jpg", pack=True),
    CommonsPhoto(
        stem="cl-cachantun-botella",
        title="File:Botella de Cachantún, MAC Quinta Normal, Santiago 20230520.jpg",
        pack=True,
    ),
    # Real Red Bull cans: a studio pack shot (the can right of centre, copy on the left)
    # and its lineup; and an unbranded cold can for Score, whose can Commons lacks.
    CommonsPhoto(
        stem="cl-red-bull-studio",
        title="File:Redbull zero 1.jpg",
        pack=True,
        region=(0.31, 0.12, 0.63, 0.98),
    ),
    CommonsPhoto(stem="cl-red-bull-lineup", title="File:Red Bull energy drinks.jpg"),
    CommonsPhoto(stem="cl-lata-fria", title="File:Drinking can ring-pull tab.jpg"),
    # The venue itself: a real Copec station in La Reina (Pronto Copec's own campaigns).
    CommonsPhoto(stem="cl-copec-estacion", title="File:Copec La Reina.jpg", focus=(0.5, 0.45)),
)

# No relit shots any more: hue-shifting an unbranded studio can into a brand's colours
# read as fake on a TV ("AI-looking"), so every brand airs a real photo instead (a real
# can where Commons has one; a real Chilean road or a real unbranded can otherwise).
RELIGHTS: tuple[Relight, ...] = ()


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


# Kept verbatim in SOURCES.md on every rebuild (the files are not downloaded here).
TEAM_SUPPLIED = (
    "Team-supplied campaign references (WhatsApp, 2026-11-22), framed by the same helpers\n"
    "(16:9 crop or pack shot) and shown only for their own brand: real product photography\n"
    "with the brand's own packaging, never edited or redrawn (CLAUDE.md invariant 4).\n"
    "`cl-domino-completos` (Dominó), `cl-starbucks-frappuccino` (Starbucks),\n"
    "`cl-guinness-pint` (Guinness 0.0), `cl-monster-hielo` (Monster), `cl-score-guarana` (Score's own ad, ALTO EN seal kept) and `cl-vital-picnic`\n"
    "(Vital). Trademarks of their owners, used for a hackathon demo without affiliation.\n\n"
)


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


def _pack(image: Image.Image, x_center: float = 0.74) -> Image.Image:
    """The whole product at 94 % of the screen height, right of centre, on its own photo
    blurred and darkened to a backdrop (the product itself is not retouched)."""
    backdrop = ImageOps.fit(image, SIZE, Image.Resampling.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(48))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.32)
    height = round(SIZE[1] * 0.94)
    width = round(image.width * height / image.height)
    product = image.resize((width, height), Image.Resampling.LANCZOS)
    left = min(max(round(SIZE[0] * x_center - width / 2), 0), SIZE[0] - width)
    # Feathered edges: the photo melts into its own blurred backdrop instead of sitting
    # on it as a pasted rectangle.
    ramp_x, ramp_y = round(width * 0.22), round(height * 0.10)
    across = [min(255, round(255 * min(x, width - 1 - x) / ramp_x)) for x in range(width)]
    down = [min(255, round(255 * min(y, height - 1 - y) / ramp_y)) for y in range(height)]
    horizontal = Image.new("L", (width, 1))
    horizontal.putdata(across)
    vertical = Image.new("L", (1, height))
    vertical.putdata(down)
    mask = ImageChops.multiply(horizontal.resize((width, height)), vertical.resize((width, height)))
    backdrop.paste(product, (left, (SIZE[1] - height) // 2), mask)
    return backdrop


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
    source = originals[photo.title]
    if photo.region is not None:
        left, top, right, bottom = photo.region
        source = source.crop(
            (
                round(left * source.width),
                round(top * source.height),
                round(right * source.width),
                round(bottom * source.height),
            )
        )
    if photo.pack:
        _save(_pack(source), photo.stem)
        return
    framed = _frame(source, photo.focus, photo.zoom)
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
            f"| {artist} | {license_name} | "
            + ("pack shot: whole, on its own blurred photo" if photo.pack else "cropped to 16:9")
            + (", cut from the wider shot" if photo.region else "")
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
        "- Real photos from Wikimedia Commons (Chilean counter food, drinks and sweets,\n"
        "  Ruta 5 and a Copec station), credited below as the licenses require. Food,\n"
        "  drink and road only: no recognisable faces, no third-party marks. CC BY-SA\n"
        "  derivatives (the crops) stay under CC BY-SA. They air in rotation and as\n"
        "  category scenes (manifest `alternates`, `category:<name>`).\n\n"
        + TEAM_SUPPLIED
        + "| File | Source | Author | License | Changes |\n|---|---|---|---|---|\n"
        + "\n".join(credits)
        + "\n"
    )
    print("photos/SOURCES.md")


if __name__ == "__main__":
    main()
