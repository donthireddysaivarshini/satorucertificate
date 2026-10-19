import shutil
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from certs.models import Batch
from certs.services import generate


class Command(BaseCommand):
    help = "Sync repository assets (fonts, templates) into MEDIA_ROOT and generate PDFs for batches."

    def add_arguments(self, parser):
        parser.add_argument("--generate", action="store_true", help="Also generate PDFs for batches.")
        parser.add_argument("--batch", type=int, help="Optional batch ID to generate (defaults to all batches).")

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT)
        templates_dir = media_root / "templates"
        fonts_dir = media_root / "fonts"
        sheets_dir = media_root / "sheets"
        templates_dir.mkdir(parents=True, exist_ok=True)
        fonts_dir.mkdir(parents=True, exist_ok=True)
        sheets_dir.mkdir(parents=True, exist_ok=True)

        base_dir = Path(settings.BASE_DIR)

        mappings = [
            ("DMSans.ttf", fonts_dir / "DMSans.ttf"),
            ("Faculty_General_UltraHD.png", templates_dir / "Faculty_General_UltraHD.png"),
            ("Training_Workshop_STW_300dpi.png", templates_dir / "Training_Workshop_STW_300dpi.png"),
            ("Training_Workshop_Completion_300dpi.png", templates_dir / "Training_Workshop_Completion_300dpi.png"),
            ("satoru_template_U9kn639.png", templates_dir / "satoru_template_U9kn639.png"),
        ]

        for f in base_dir.glob("*.csv"):
            mappings.append((f.name, sheets_dir / f.name))
        for f in base_dir.glob("*.xlsx"):
            mappings.append((f.name, sheets_dir / f.name))

        for src_name, dst_path in mappings:
            src_path = base_dir / src_name
            if src_path.exists():
                shutil.copy2(src_path, dst_path)
                self.stdout.write(self.style.SUCCESS(f"Synced {src_name} -> {dst_path}"))
            else:
                self.stdout.write(self.style.WARNING(f"Source asset not found: {src_path}"))

        if options["generate"]:
            from certs.services import import_sheet
            batches_qs = Batch.objects.all()
            if options.get("batch"):
                batches_qs = batches_qs.filter(id=options["batch"])
            for batch in batches_qs.order_by("id"):
                if not batch.template or not batch.template.image:
                    self.stdout.write(self.style.WARNING(f"Skipping Batch {batch.id}: no template image"))
                    continue
                template_path = Path(batch.template.image.path)
                if not template_path.exists():
                    self.stdout.write(self.style.WARNING(f"Skipping Batch {batch.id}: template image not found at {template_path}"))
                    continue

                # If batch has no certificates yet, try importing from sheet
                if batch.certificates.count() == 0 and batch.sheet:
                    sheet_path = Path(batch.sheet.path)
                    if sheet_path.exists():
                        self.stdout.write(f"Importing sheet for Batch {batch.id}...")
                        import_sheet(batch)
                    else:
                        self.stdout.write(self.style.WARNING(f"Batch {batch.id} sheet not found at {sheet_path}"))

                if batch.certificates.count() == 0:
                    self.stdout.write(self.style.WARNING(f"Skipping Batch {batch.id}: 0 certificates"))
                    continue

                batch_certs_dir = media_root / "certificates" / str(batch.id)
                batch_certs_dir.mkdir(parents=True, exist_ok=True)

                # Clear stale generation errors
                if batch.issues and "errors" in batch.issues:
                    batch.issues["errors"] = [e for e in batch.issues["errors"] if not str(e).startswith("Generating PDFs failed")]
                    batch.save(update_fields=["issues"])

                self.stdout.write(f"Generating PDFs for Batch {batch.id}: {batch.title} ({batch.certificates.count()} certs)...")
                try:
                    generate(batch)
                    self.stdout.write(self.style.SUCCESS(f"Generated PDFs for Batch {batch.id}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to generate Batch {batch.id}: {e}"))
