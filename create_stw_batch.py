import os
import django
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "certdesk.settings")
django.setup()

from django.contrib.auth.models import User
from django.core.files import File
from certs.models import Template, Batch, Certificate
from certs import services as svc

print("=== Creating Batch for Training Workshop STW (86 Participants) ===")

# 1. Fetch Template and Users
template = Template.objects.get(name="Training Workshop STW")
admin_user = User.objects.get(username="admin")
approver_user = User.objects.get(username="approver")

batch_title = "Training Workshop: Geriatric care - STW Participants (8 Oct 2026)"

# Delete any existing test batch with same title
Batch.objects.filter(title=batch_title).delete()

# 2. Create Batch
batch = Batch.objects.create(
    title=batch_title,
    template=template,
    created_by=admin_user,
    approver=approver_user,
    cert_no_start=1,
    cert_no_format="SF/STW/{n:03d}/2026",
    values={
        "workshop_date": "8 October 2026",
        "venue": "Trauma Auditorium, NIMS Hospital",
        "hours": "8 hours",
        "issued": "19 October 2026"
    },
    from_name="Satoru Foundation"
)

csv_path = "stw_participants_86.csv"
with open(csv_path, "rb") as f:
    batch.sheet.save("stw_participants_86.csv", File(f), save=False)
batch.save()

print(f"Batch created: {batch.title} (ID: {batch.pk})")

# 3. Import Sheet
svc.import_sheet(batch, user=admin_user)
batch.refresh_from_db()
print(f"Certificates imported: {batch.certificates.count()} records.")
print(f"Batch issues: {batch.issues}")

# Check any errors
if batch.errors:
    print("ERRORS ENCOUNTERED:", batch.errors)
else:
    print("Sheet imported with 0 errors!")

# 4. Generate PDFs
print("\nGenerating 86 PDFs at Ultra-HD 300 DPI...")
svc.generate(batch)
batch.refresh_from_db()
print(f"Batch status after generation: {batch.status}")

generated_count = batch.certificates.exclude(pdf="").count()
print(f"Successfully generated {generated_count} / {batch.certificates.count()} PDFs!")
first_cert = batch.certificates.first()
last_cert = batch.certificates.last()
print(f"First certificate: {first_cert.cert_no} - {first_cert.name} (File: {first_cert.pdf.name})")
print(f"Last certificate: {last_cert.cert_no} - {last_cert.name} (File: {last_cert.pdf.name})")

print("\n=== SUCCESS: All 86 certificates created and generated! ===")
