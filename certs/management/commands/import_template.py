import tomllib
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from certs.models import Font, Template
from certs.render import legacy_to_box


def hex_colour(c):
    if isinstance(c, list) and len(c) == 3:
        return "#{:02x}{:02x}{:02x}".format(*c)
    return c


def nice_label(text):
    import re
    m = re.findall(r"\{(\w+)", text)
    return (m[-1] if m else text).replace("_", " ").capitalize()[:40] or "Text"


class Command(BaseCommand):
    help = "Create a template from a certkit event.toml (layout, values, email) plus its image and font."

    def add_arguments(self, parser):
        parser.add_argument("toml", help="path to event.toml")
        parser.add_argument("--image", required=True, help="template image (PNG/JPG)")
        parser.add_argument("--font", help="font file (.ttf/.otf) to use as the default font")
        parser.add_argument("--name", help="template name (default: title from the toml)")

    def handle(self, toml, image, font=None, name=None, **opts):
        toml_path = Path(toml)
        with open(toml_path, "rb") as fh:
            cfg = tomllib.load(fh)
        name = name or cfg.get("title") or toml_path.parent.name
        if Template.objects.filter(name=name).exists():
            raise CommandError(f"A template called '{name}' already exists.")

        font_obj = None
        if font:
            fp = Path(font)
            font_obj, created = Font.objects.get_or_create(name=fp.stem)
            if created or not font_obj.file:
                with open(fp, "rb") as fh:
                    font_obj.file.save(fp.name, File(fh), save=True)

        style = cfg.get("style", {})
        fields = []
        for i, f in enumerate(cfg.get("fields", []), 1):
            fld = {
                "id": f"f{i}", "type": "text", "label": nice_label(f.get("text", "")),
                "text": f.get("text", ""), "x": f["x"], "y": f["y"],
                "anchor": f.get("anchor", "ls"), "size": f.get("size", style.get("size", 28)),
                "weight": f.get("weight", style.get("weight", "Regular")),
                "color": hex_colour(f.get("color", style.get("color", [0, 0, 0]))),
            }
            for k in ("max_width", "min_size", "case", "cover"):
                if k in f:
                    fld[k] = f[k]
            fields.append(legacy_to_box(fld, i))

        email = cfg.get("email", {})
        body = ""
        if email.get("body_file") and (toml_path.parent / email["body_file"]).exists():
            body = (toml_path.parent / email["body_file"]).read_text(encoding="utf-8")

        t = Template(
            name=name, default_font=font_obj, fields=fields,
            default_values={k: str(v) for k, v in cfg.get("values", {}).items()},
            cert_no_format=cfg.get("cert_no", {}).get("format", "{n}"),
            resolution=cfg.get("output", {}).get("resolution", 150),
        )
        if email.get("subject"):
            t.email_subject = email["subject"]
        if body:
            t.email_body = body
        img = Path(image)
        with open(img, "rb") as fh:
            t.image.save(img.name, File(fh), save=False)
        t.save()
        self.stdout.write(self.style.SUCCESS(f"Created template '{t.name}' with {len(fields)} fields. "
                                             "Open its layout editor to check the boxes and add the QR code."))
