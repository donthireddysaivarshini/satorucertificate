"""
Certificate rendering: draws text and QR fields onto a template image.

A layout is a list of field dicts (stored on Template.fields):

  text: {"type": "text", "label": "Name", "text": "{name}", "x": 1315, "y": 598,
         "anchor": "ms", "size": 64, "weight": "SemiBold", "font": <Font id or null>,
         "color": "#3d3b3a", "max_width": 1000, "min_size": 18, "case": "",
         "cover": [x0, y0, x1, y1] or null}
  qr:   {"type": "qr", "label": "QR code", "data": "{verify_url}", "x": 1700, "y": 1100,
         "size": 200, "color": "#000000", "background": "#ffffff"}

x/y are pixels on the template image. For text, anchor is Pillow's two-letter
anchor (l/m/r + s=baseline); for QR, x/y is the top-left corner.
"""
import io
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PLACEHOLDER_RE = re.compile(r"\{(\w+)(?:[^}]*)\}")
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fill(template, values):
    """Fill {placeholders}; unknown ones stay visible as {like_this}."""
    try:
        return str(template).format_map(SafeDict(values))
    except (ValueError, IndexError, KeyError):
        return str(template)


def placeholders(text):
    return set(PLACEHOLDER_RE.findall(str(text or "")))


BOX_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
MIN_BOX_SIZE = 14  # same as MIN_SIZE in the layout editor


def norm_key(key):
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def is_box(f):
    """Fields made by the layout editor are boxes (x, y, w, h)."""
    return "w" in f and "h" in f


def fill_pattern(pattern, values):
    """{Column Heading} or {value_name} -> value. Unknown ones stay visible."""
    def sub(m):
        v = values.get(norm_key(m.group(1)))
        return m.group(0) if v in (None, "") else str(v)
    return BOX_PLACEHOLDER_RE.sub(sub, str(pattern or ""))


def box_text(f, values):
    src = f.get("source", "fixed")
    if src == "column":
        return str(values.get(norm_key(f.get("column", ""))) or "")
    if src == "pattern":
        return fill_pattern(f.get("pattern", ""), values)
    if src == "qr":
        return fill_pattern(f.get("pattern") or "{verify_url}", values)
    return fill_pattern(f.get("text", ""), values)


def box_placeholders(f):
    """Normalised value names a box field needs."""
    src = f.get("source", "fixed")
    if src == "column":
        return {norm_key(f.get("column", ""))} - {""}
    if src == "qr":
        text = f.get("pattern") or "{verify_url}"
    elif src == "pattern":
        text = f.get("pattern", "")
    else:
        text = f.get("text", "")
    return {norm_key(m) for m in BOX_PLACEHOLDER_RE.findall(str(text or ""))}


def legacy_to_box(f, i=1):
    """Convert an older anchor-point field (x/y = baseline point) into an editor box."""
    if is_box(f):
        return f
    label = f.get("label") or "Field %d" % i
    if f.get("type") == "qr":
        s = int(f.get("size") or 200)
        return {"key": norm_key(label) or "qr", "label": label, "source": "qr",
                "pattern": f.get("data") or "{verify_url}", "x": int(f["x"]), "y": int(f["y"]), "w": s, "h": s,
                "align": "center", "size": 28, "weight": "Regular", "color": f.get("color") or "#000000"}
    size = int(f.get("size") or 28)
    text = f.get("text", "")
    m = re.fullmatch(r"\{(\w+)\}", text.strip())
    out = {"key": norm_key(label) or "field_%d" % i, "label": label, "size": size,
           "weight": f.get("weight") or "Regular", "color": f.get("color") or "#3d3b3a"}
    if isinstance(out["color"], list):
        out["color"] = "#{:02x}{:02x}{:02x}".format(*out["color"])
    if m:
        out.update(source="column", column=m.group(1))
    else:
        out.update(source="pattern", pattern=text)
    a = (f.get("anchor") or "ls")[0]
    out["align"] = {"m": "center", "r": "right"}.get(a, "left")
    cover = f.get("cover")
    if cover and len(cover) == 4:
        x0, y0, x1, y1 = (int(v) for v in cover)
        out.update(x=min(x0, x1), y=min(y0, y1), w=abs(x1 - x0), h=abs(y1 - y0), fill=f.get("cover_color") or "#ffffff")
        if isinstance(out["fill"], list):
            out["fill"] = "#{:02x}{:02x}{:02x}".format(*out["fill"])
        return out
    w = int(f.get("max_width") or 520)
    h = round(size * 1.6)
    x = int(f["x"])
    x0 = x - w // 2 if a == "m" else x - w if a == "r" else x
    mid = int(f["y"]) - round(size * 0.35)  # baseline -> middle of the letters
    out.update(x=x0, y=mid - h // 2, w=w, h=h)
    return out


def fallback_font():
    for p in FALLBACK_FONTS:
        if Path(p).exists():
            return p
    return None


@lru_cache(maxsize=512)
def get_font(path, size, weight):
    if not path:
        return ImageFont.load_default(size)
    f = ImageFont.truetype(path, size)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass  # static font, or a weight the font doesn't have
    return f


@lru_cache(maxsize=16)
def _load_image(path, mtime):
    return Image.open(path).convert("RGB")


def load_image(path):
    path = str(path)
    return _load_image(path, Path(path).stat().st_mtime)


def color_of(c, default=(0, 0, 0)):
    if not c:
        return default
    if isinstance(c, (list, tuple)):
        return tuple(int(v) for v in c)
    return c


def qr_image(data, size, color="#000000", background="#ffffff", border=1):
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    q = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=border)
    q.add_data(data)
    q.make(fit=True)
    img = q.make_image(fill_color=color, back_color=background).get_image().convert("RGB")
    return img.resize((size, size), Image.NEAREST)


def render_box(img, d, f, values, path, override=None):
    x, y, w, h = (int(f.get(k, 0)) for k in ("x", "y", "w", "h"))
    if f.get("fill"):
        d.rectangle((x, y, x + w - 1, y + h - 1), fill=color_of(f["fill"], "white"))
    text = override if override is not None else box_text(f, values)
    if f.get("source") == "qr":
        if text:
            s = max(20, min(w, h))
            q = qr_image(text, s, f.get("color") or "#000000", f.get("background") or "#ffffff")
            img.paste(q, (x + (w - s) // 2, y + (h - s) // 2))
        return
    text = str(text or "").strip()
    if not text:
        return
    weight = f.get("weight") or "Regular"
    size = int(f.get("size") or 28)
    font = get_font(path, size, weight)
    while size > MIN_BOX_SIZE and d.textlength(text, font=font) > w:
        size -= 1
        font = get_font(path, size, weight)
    align = f.get("align") or "left"
    cx = x + w / 2 if align == "center" else x + w if align == "right" else x
    anchor = {"center": "mm", "right": "rm"}.get(align, "lm")
    d.text((cx, y + h / 2), text, font=font, fill=color_of(f.get("color")), anchor=anchor)


def render(base, fields, values, font_paths=None, default_font=None, overrides=None):
    """
    Return a new image with every field drawn. font_paths maps Font id -> file path.
    overrides maps a box field's key to the exact text to print (used by the editor preview).
    """
    font_paths = font_paths or {}
    overrides = overrides or {}
    default_path = default_font or fallback_font()
    img = base.copy()
    d = ImageDraw.Draw(img)
    for fld in fields:
        if is_box(fld):
            path = font_paths.get(str(fld.get("font"))) if fld.get("font") else None
            render_box(img, d, fld, values, path or default_path, overrides.get(fld.get("key")))
            continue
        cover = fld.get("cover")
        if cover and len(cover) == 4:
            x0, y0, x1, y1 = (int(v) for v in cover)
            d.rectangle((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)),
                        fill=color_of(fld.get("cover_color"), "white"))

        if fld.get("type") == "qr":
            data = fill(fld.get("data") or "{verify_url}", values)
            size = max(40, int(fld.get("size") or 200))
            q = qr_image(data, size, fld.get("color") or "#000000", fld.get("background") or "#ffffff")
            img.paste(q, (int(fld.get("x", 0)), int(fld.get("y", 0))))
            continue

        text = fill(fld.get("text", ""), values).strip()
        if not text:
            continue
        case = fld.get("case")
        if case == "upper":
            text = text.upper()
        elif case == "title":
            text = text.title()
        path = font_paths.get(str(fld.get("font"))) if fld.get("font") else None
        path = path or default_path
        weight = fld.get("weight") or "Regular"
        size = int(fld.get("size") or 28)
        min_size = int(fld.get("min_size") or 14)
        font = get_font(path, size, weight)
        max_w = fld.get("max_width")
        while max_w and size > min_size and d.textlength(text, font=font) > float(max_w):
            size -= 1
            font = get_font(path, size, weight)
        d.text((int(fld.get("x", 0)), int(fld.get("y", 0))), text, font=font,
               fill=color_of(fld.get("color")), anchor=fld.get("anchor") or "ls")
    return img


def draw_guides(img, fields, selected=None):
    """Faint max-width lines and cover boxes, for the layout editor preview."""
    d = ImageDraw.Draw(img)
    for i, fld in enumerate(fields):
        if is_box(fld):
            continue
        col = (37, 99, 235) if i == selected else (148, 163, 184)
        cover = fld.get("cover")
        if cover and len(cover) == 4:
            x0, y0, x1, y1 = (int(v) for v in cover)
            d.rectangle((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), outline=(234, 88, 12), width=2)
        if fld.get("type") == "qr":
            s = int(fld.get("size") or 200)
            x, y = int(fld.get("x", 0)), int(fld.get("y", 0))
            d.rectangle((x, y, x + s, y + s), outline=col, width=2)
            continue
        mw = fld.get("max_width")
        if mw:
            mw, x, y = float(mw), int(fld.get("x", 0)), int(fld.get("y", 0))
            a = (fld.get("anchor") or "ls")[0]
            x0 = x - mw / 2 if a == "m" else x - mw if a == "r" else x
            d.line([(x0, y + 8), (x0 + mw, y + 8)], fill=col, width=3)
            d.line([(x0, y), (x0, y + 16)], fill=col, width=3)
            d.line([(x0 + mw, y), (x0 + mw, y + 16)], fill=col, width=3)
    return img


def to_pdf_bytes(img, resolution=150, quality=100):
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=resolution, quality=quality, subsampling=0)
    return buf.getvalue()


def to_jpeg_bytes(img, max_width=1400, quality=85):
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()
