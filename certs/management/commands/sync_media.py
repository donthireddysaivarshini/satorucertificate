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

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT)
        templates_dir = media_root / "templates"
        fonts_dir = media_root / "fonts"
        templates_dir.mkdir(parents=True, exist_ok=True)
        fonts_dir.mkdir(parents=True, exist_ok=True)

        base_dir = Path(settings.BASE_DIR)

        mappings = [
            ("DMSans.ttf", fonts_dir / "DMSans.ttf"),
            ("Faculty_General_UltraHD.png", templates_dir / "Faculty_General_UltraHD.png"),
            ("Training_Workshop_STW_300dpi.png", templates_dir / "Training_Workshop_STW_300dpi.png"),
            ("Training_Workshop_Completion_300dpi.png", templates_dir / "Training_Workshop_Completion_300dpi.png"),
            ("satoru_template_U9kn639.png", templates_dir / "satoru_template_U9kn639.png"),
        ]

        for src_name, dst_path in mappings:
            src_path = base_dir / src_name
            if src_path.exists() and not dst_path.exists():
                shutil.copy2(src_path, dst_path)
                self.stdout.write(self.style.SUCCESS(f"Copied {src_name} -> {dst_path}"))
            elif dst_path.exists():
                self.stdout.write(f"Already exists: {dst_path}")
            else:
                self.stdout.write(self.style.WARNING(f"Source asset not found: {src_path}"))

        if options["generate"]:
            for batch in Batch.objects.filter(certificates__isnull=False).distinct().order_by("id"):
                if not batch.template or not batch.template.image:
                    self.stdout.write(self.style.WARNING(f"Skipping Batch {batch.id}: no template image"))
                    continue
                template_path = Path(batch.template.image.path)
                if not template_path.exists():
                    self.stdout.write(self.style.WARNING(f"Skipping Batch {batch.id}: template image not found at {template_path}"))
                    continue

                batch_certs_dir = media_root / "certificates" / str(batch.id)
                batch_certs_dir.mkdir(parents=True, exist_ok=True)

                self.stdout.write(f"Generating PDFs for Batch {batch.id}: {batch.title}...")
                try:
                    generate(batch)
                    self.stdout.write(self.style.SUCCESS(f"Generated PDFs for Batch {batch.id}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed to generate Batch {batch.id}: {e}"))
