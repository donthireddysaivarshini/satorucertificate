import io
import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "certdesk.settings")
import django
django.setup()

from django.contrib.auth.models import User, Group
from django.core.files.base import ContentFile
from certs.models import Template, Batch, Certificate, Font
from certs import services as svc
from certs.render import FALLBACK_FONTS

print("=== Starting Satoru Certificate Generator Test on Neon PostgreSQL ===")

# 1. Ensure Roles & Users
print("\n[Step 1] Setting up users & roles...")
organisers_group = Group.objects.get(name="Organisers")
approvers_group = Group.objects.get(name="Approvers")

admin_user, _ = User.objects.get_or_create(
    username="admin",
    defaults={"email": "admin@satorufoundation.org", "is_staff": True, "is_superuser": True}
)
admin_user.set_password("admin123")
admin_user.is_staff = True
admin_user.is_superuser = True
admin_user.save()
admin_user.groups.add(organisers_group)

approver_user, _ = User.objects.get_or_create(
    username="approver",
    defaults={"email": "approver@satorufoundation.org", "is_staff": True}
)
approver_user.set_password("approver123")
approver_user.is_staff = True
approver_user.save()
approver_user.groups.add(approvers_group)

print(f"Users ready: admin (superuser/organiser), approver (approver)")

# 2. Create Certificate Background Image
print("\n[Step 2] Generating certificate template image...")
width, height = 2000, 1414
bg = Image.new("RGB", (width, height), "#fbfbfe")
draw = ImageDraw.Draw(bg)

# Draw elegant borders
draw.rectangle([(40, 40), (width - 40, height - 40)], outline="#1e293b", width=6)
draw.rectangle([(60, 60), (width - 60, height - 60)], outline="#6366f1", width=2)

# Draw Title & Headers on the base canvas
try:
    font_large = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 68)
    font_sub = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 36)
except Exception:
    font_large = ImageFont.load_default()
    font_sub = ImageFont.load_default()

draw.text((width // 2, 220), "CERTIFICATE OF ACHIEVEMENT", fill="#0f172a", font=font_large, anchor="mm")
draw.text((width // 2, 320), "This is proudly presented to", fill="#64748b", font=font_sub, anchor="mm")
draw.text((width // 2, 700), "for successfully completing the advanced workshop on", fill="#64748b", font=font_sub, anchor="mm")
draw.text((width // 2, 770), "Satoru Cloud & System Architecture", fill="#4338ca", font=font_large, anchor="mm")

img_buffer = io.BytesIO()
bg.save(img_buffer, "PNG")
img_bytes = img_buffer.getvalue()

# Clean up any existing test batches/templates so the test is idempotent
Batch.objects.filter(title__startswith="Satoru Architecture Masterclass").delete()
Template.objects.filter(name="Satoru Engineering Workshop").delete()

# 3. Create Template
print("\n[Step 3] Registering Template...")
fields = [
    {
        "key": "name", "label": "Participant Name", "source": "column", "column": "Name",
        "x": 300, "y": 480, "w": 1400, "h": 120, "align": "center", "size": 64,
        "weight": "Bold", "color": "#1e1b4b"
    },
    {
        "key": "college", "label": "College", "source": "column", "column": "College",
        "x": 300, "y": 860, "w": 1400, "h": 50, "align": "center", "size": 32,
        "weight": "Regular", "color": "#334155"
    },
    {
        "key": "date", "label": "Workshop Date", "source": "pattern",
        "pattern": "Date: {workshop_date}   |   Duration: {hours}",
        "x": 300, "y": 940, "w": 1400, "h": 40, "align": "center", "size": 24,
        "weight": "Regular", "color": "#64748b"
    },
    {
        "key": "cert_no", "label": "Certificate No", "source": "pattern",
        "pattern": "Certificate No: {cert_no}",
        "x": 120, "y": 1250, "w": 600, "h": 40, "align": "left", "size": 22,
        "weight": "Regular", "color": "#475569"
    },
    {
        "key": "qr", "label": "QR Code", "source": "qr",
        "x": 1700, "y": 1130, "w": 180, "h": 180, "align": "center",
        "color": "#0f172a"
    }
]

template, _ = Template.objects.get_or_create(name="Satoru Engineering Workshop")
template.fields = fields
template.cert_no_format = "SF/2026/{n:04d}"
template.default_values = {"workshop_date": "15 October 2026", "hours": "8 Hours"}
template.image.save("satoru_template.png", ContentFile(img_bytes), save=False)
template.save()
print(f"Template '{template.name}' saved with {len(fields)} fields.")

# 4. Create Batch with CSV participants
print("\n[Step 4] Creating Batch and uploading participants...")
csv_content = """Name,Email,College Name
Sai Venkat,dsaiv@example.com,Indian Institute of Technology
Aarav Sharma,aarav@example.com,National Institute of Technology
Ananya Verma,ananya@example.com,BITS Pilani
"""

batch = Batch.objects.create(
    title="Satoru Architecture Masterclass Batch 1",
    template=template,
    created_by=admin_user,
    approver=approver_user,
    from_name="Satoru Foundation",
    cert_no_start=1
)
batch.sheet.save("participants.csv", ContentFile(csv_content.encode("utf-8")), save=False)
batch.save()

# Import the sheet
svc.import_sheet(batch, user=admin_user)
print(f"Batch created: {batch.title} (ID: {batch.pk})")
print(f"Certificates imported: {batch.certificates.count()} records.")
for c in batch.certificates.all():
    print(f"  - Row {c.row}: {c.name} | {c.email} | Cert No: {c.cert_no} | Code: {c.code}")

# 5. Generate PDFs
print("\n[Step 5] Generating certificate PDFs...")
svc.generate(batch)
batch.refresh_from_db()
print(f"Batch status after generation: {batch.status}")

for c in batch.certificates.all():
    pdf_exists = bool(c.pdf and Path(c.pdf.path).exists())
    pdf_size = Path(c.pdf.path).stat().st_size if pdf_exists else 0
    print(f"  - Certificate {c.cert_no} for {c.name}: PDF path={c.pdf.name}, exists={pdf_exists}, size={pdf_size} bytes")

# 6. Approval Workflow
print("\n[Step 6] Submitting and Approving Batch...")
svc.submit(batch, user=admin_user)
batch.refresh_from_db()
print(f"Status after submit: {batch.status} (Waiting for approver: {batch.approver.username})")

svc.approve(batch, user=approver_user, comment="Looks great! Formatting and names verified.")
batch.refresh_from_db()
print(f"Status after approval: {batch.status} (Decided at: {batch.decided_at})")

# 7. Verification Test
print("\n[Step 7] Testing Verification...")
from django.test import Client
client = Client()

first_cert = batch.certificates.first()
qr_url = f"/v/{first_cert.code}/"
response = client.get(qr_url)
print(f"GET {qr_url} -> Status {response.status_code}")
assert response.status_code == 200, f"Expected 200, got {response.status_code}"
assert first_cert.name.encode() in response.content, "Certificate name should appear on verify page"
assert first_cert.cert_no.encode() in response.content, "Certificate number should appear on verify page"

# Search by certificate number + name
search_url = f"/verify/?q={first_cert.cert_no}&name={first_cert.name}"
response_search = client.get(search_url)
print(f"GET {search_url} -> Status {response_search.status_code} (Redirect: {response_search.url if response_search.status_code == 302 else 'No'})")
assert response_search.status_code == 302, f"Expected 302 redirect to /v/{first_cert.code}/"

print("\n=== SUCCESS: All tests passed! Certificate Generation & Verification is working! ===")
