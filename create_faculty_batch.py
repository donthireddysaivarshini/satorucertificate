import os
import django
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "certdesk.settings")
django.setup()

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from certs.models import Template, Batch, Certificate
from certs import services as svc

print("=== Creating Batch for Faculty General STG ===")

# 1. Fetch Template and Users
template = Template.objects.get(name="Faculty General STG")
admin_user = User.objects.get(username="admin")
approver_user = User.objects.get(username="approver")

# 2. Prepare CSV sheet
csv_content = """Name,Email ID
YERUVA SUNITHA REDDY,yeruvasunithareddy@gmail.com
SUNITHA GODASI,disha-nimi@yahoo.com
RIT JHALAK,rit.99majhi@gmail.com
Y. Venkatalakshmi,y.venkatalakshmi04@gmail.com
"""

# 3. Create Batch
batch_title = "Faculty Workshop: Geriatric care - NIMS Hyderabad (8 Oct 2026)"

# Remove existing batch with same title if present
Batch.objects.filter(title=batch_title).delete()

batch = Batch.objects.create(
    title=batch_title,
    template=template,
    created_by=admin_user,
    approver=approver_user,
    cert_no_start=1,
    values={
        "workshop_date": "8 October 2026",
        "venue": "NIMS, Hyderabad",
        "hours": "8 hours",
        "issued": "16 October 2026"
    },
    from_name="Satoru Foundation"
)

batch.sheet.save("faculty_participants.csv", ContentFile(csv_content.encode("utf-8")), save=False)
batch.save()
print(f"Batch created: {batch.title} (ID: {batch.pk})")

# 4. Import the sheet
svc.import_sheet(batch, user=admin_user)
batch.refresh_from_db()
print(f"Sheet imported with {batch.certificates.count()} certificates.")
print(f"Issues: {batch.issues}")

for c in batch.certificates.all():
    print(f"  - Row {c.row}: {c.name} | {c.email} | Cert No: {c.cert_no} | Serial: {c.serial} | Code: {c.code}")

# 5. Generate PDFs
print("\nGenerating PDFs...")
svc.generate(batch)
batch.refresh_from_db()
print(f"Batch status after generation: {batch.status}")

for c in batch.certificates.all():
    pdf_exists = bool(c.pdf and Path(c.pdf.path).exists())
    pdf_size = Path(c.pdf.path).stat().st_size if pdf_exists else 0
    print(f"  - Certificate {c.cert_no} for {c.name}: PDF path={c.pdf.name}, exists={pdf_exists}, size={pdf_size} bytes")

print("\n=== SUCCESS: Faculty certificates generated successfully! ===")
