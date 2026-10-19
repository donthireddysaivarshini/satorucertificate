import os
import csv
import shutil
from pathlib import Path
from PIL import Image

# 1. Upscale both user-uploaded master templates to 3508x2480 (300 DPI A4)
p_comp_src = r"C:\Users\dsaiv\.gemini\antigravity-ide\brain\f3756271-cc9c-4a24-8a8a-62a1cee05a11\.user_uploaded\media_1792392737395.png"
p_part_src = r"C:\Users\dsaiv\.gemini\antigravity-ide\brain\f3756271-cc9c-4a24-8a8a-62a1cee05a11\.user_uploaded\media_1792392737400.png"

im_comp = Image.open(p_comp_src).resize((3508, 2480), Image.Resampling.LANCZOS)
im_comp.save("Training_Workshop_Completion_300dpi.png", dpi=(300, 300), quality=100)
print("Saved Training_Workshop_Completion_300dpi.png:", im_comp.size)

im_part = Image.open(p_part_src).resize((3508, 2480), Image.Resampling.LANCZOS)
im_part.save("Training_Workshop_STW_300dpi.png", dpi=(300, 300), quality=100)
print("Saved Training_Workshop_STW_300dpi.png:", im_part.size)

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "certdesk.settings")
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from certs.models import Template, Batch, Certificate, Font
from certs.services import generate

User = get_user_model()
admin_user = User.objects.filter(is_superuser=True).first()

media_root = Path(settings.MEDIA_ROOT)
media_templates = media_root / "templates"
media_sheets = media_root / "sheets"
media_templates.mkdir(parents=True, exist_ok=True)
media_sheets.mkdir(parents=True, exist_ok=True)

# Copy templates to media/templates
shutil.copy2("Training_Workshop_Completion_300dpi.png", media_templates / "Training_Workshop_Completion_300dpi.png")
shutil.copy2("Training_Workshop_STW_300dpi.png", media_templates / "Training_Workshop_STW_300dpi.png")

dm_font = Font.objects.filter(name__icontains="DM Sans").first()

# Mail contents
mail_part_subject = "Certificate of Participation: Geriatric Care Workshop at NIMS Hospital Conducted by Satoru Foundation"
mail_part_body = """Respected {name},

Thank you for attending our Geriatric Care Workshop at NIMS Hospital, Punjagutta, on 8th October. We appreciate your participation in the session.

Please find attached your Certificate of Participation, in recognition of your attendance.

We hope you found the workshop informative and valuable, and we look forward to your participation in our future events.

Warm regards,
Kanagala Sai Charan
Satoru Foundation
Director & Founder
9959485500
office@satorufoundation.org"""

mail_comp_subject = "Your Certificate of Completion: Geriatric Care Workshop (NIMS) conducted by SATORU FOUNDATION"
mail_comp_body = """Dear Participant,

Thank you for attending our Geriatric Care Workshop at NIMS Hospital, Punjagutta, on 8th October. We truly appreciate your active participation throughout the session.

Please find attached your Certificate of Completion, awarded in recognition of your attendance and successful completion of both the Pre-Workshop and Post-Workshop Quizzes.

We hope you found the workshop informative and valuable to your practice. We look forward to welcoming you to our future events.

Warm regards,

Kanagala Sai Charan

Director & Founder, Satoru Foundation

9959485500

office@satorufoundation.org"""

# Common layout fields (from Template 4)
base_t4 = Template.objects.get(pk=4)
layout_fields = base_t4.fields

# Update Template 4 (Participation)
base_t4.image.name = "templates/Training_Workshop_STW_300dpi.png"
base_t4.default_font = dm_font
base_t4.resolution = 300
base_t4.email_subject = mail_part_subject
base_t4.email_body = mail_part_body
base_t4.save()
print("Updated Template 4 (Participation).")

# Create or Update Completion Template
comp_template = Template.objects.filter(name="Training Workshop STW - Completion").first()
if not comp_template:
    comp_template = Template.objects.create(
        name="Training Workshop STW - Completion",
        image="templates/Training_Workshop_Completion_300dpi.png",
        default_font=dm_font,
        resolution=300,
        cert_no_format="SF/STW/{n:03d}/2026",
        fields=layout_fields,
        email_subject=mail_comp_subject,
        email_body=mail_comp_body
    )
else:
    comp_template.image.name = "templates/Training_Workshop_Completion_300dpi.png"
    comp_template.default_font = dm_font
    comp_template.resolution = 300
    comp_template.fields = layout_fields
    comp_template.email_subject = mail_comp_subject
    comp_template.email_body = mail_comp_body
    comp_template.save()
print("Created/Updated Completion Template:", comp_template.pk)


# =============================================================
# 1. PARTICIPATION BATCH (Batch 5): 79 members + 1 test member
# =============================================================
print("\n--- Processing Participation Batch (Batch 5) ---")
completion_emails = {
    "tumeshpetarupika@gmail.com",
    "biradharsonika2006@gmail.com",
    "sangeetha94559@gmail.com",
    "sfatima15102005@gmail.com",
    "nishatmohammadi3@gmail.com",
    "jyothichouhan557@gmail.com",
}

with open("stw_participants_86.csv", "r", encoding="utf-8") as f:
    raw_rows = list(csv.DictReader(f))

filtered_participation = []
for r in raw_rows:
    if r["Email"].strip().lower() not in completion_emails:
        filtered_participation.append(r)

# Write participation CSV with test participant
csv_part_path = Path("stw_participants_with_test.csv")
with open(csv_part_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["Serial No", "Full Name", "Email"])
    for idx, r in enumerate(filtered_participation, 1):
        writer.writerow([idx, r["Full Name"].strip(), r["Email"].strip()])
    # Add test user as 80th row
    writer.writerow(["T1", "SAITEST", "dsaivarshini30@gmail.com"])

shutil.copy2(csv_part_path, media_sheets / "stw_participants_with_test.csv")

batch_5 = Batch.objects.get(pk=5)
batch_5.title = "Training Workshop: Geriatric care - STW Participation (79 + 1 Test)"
batch_5.sheet.name = "sheets/stw_participants_with_test.csv"
batch_5.email_subject = mail_part_subject
batch_5.email_body = mail_part_body
batch_5.values = {
    "workshop_date": "8 October 2026",
    "venue": "Trauma Auditorium, NIMS Hospital",
    "hours": "8 hours",
    "issued": "19 October 2026"
}
batch_5.save()

# Clear existing certificates for Batch 5
batch_5.certificates.all().delete()
certs_part = []
for idx, r in enumerate(filtered_participation, 1):
    certs_part.append(Certificate(
        batch=batch_5,
        row=idx,
        serial=str(idx),
        name=r["Full Name"].strip(),
        email=r["Email"].strip(),
        cert_no=f"SF/STW/{idx:03d}/2026",
        data={}
    ))
# Test certificate
certs_part.append(Certificate(
    batch=batch_5,
    row=len(filtered_participation) + 1,
    serial="T1",
    name="SAITEST",
    email="dsaivarshini30@gmail.com",
    cert_no="SF/STW/T01-PART/2026",
    data={"College Name": "MRECW"}
))
Certificate.objects.bulk_create(certs_part)
print(f"Created {len(certs_part)} certificates in Batch 5. Rendering PDFs...")
generate(batch_5)
print(f"Batch 5 completed with status: {batch_5.status}")


# =============================================================
# 2. COMPLETION BATCH (Batch 6): 6 members + 1 test member
# =============================================================
print("\n--- Processing Completion Batch ---")
completion_list = [
    {"serial": "130", "name": "Rupika Tumeshpeta", "email": "tumeshpetarupika@gmail.com", "college": "Nims college of nursing"},
    {"serial": "131", "name": "Biradhar Sonika", "email": "biradharsonika2006@gmail.com", "college": "Apollo college of nursing"},
    {"serial": "132", "name": "K.Sangeetha", "email": "sangeetha94559@gmail.com", "college": "Nims institute of medical sciences"},
    {"serial": "133", "name": "Syeda Fatima", "email": "sfatima15102005@gmail.com", "college": "Apollo college of nursing, jublee hills , hyderabad"},
    {"serial": "134", "name": "Nishat Mohammadi", "email": "nishatmohammadi3@gmail.com", "college": "Apollo college of nursing jubilee hills hyderabad"},
    {"serial": "135", "name": "Jyothi Chouhan", "email": "jyothichouhan557@gmail.com", "college": "Apollo college of nursing"},
]

csv_comp_path = Path("stw_completion_with_test.csv")
with open(csv_comp_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["Serial No", "Full Name", "Email", "College Name"])
    for r in completion_list:
        writer.writerow([r["serial"], r["name"], r["email"], r["college"]])
    writer.writerow(["T1", "SAITEST", "dsaivarshini30@gmail.com", "MRECW"])

shutil.copy2(csv_comp_path, media_sheets / "stw_completion_with_test.csv")

batch_comp_title = "Training Workshop: Geriatric care - Completion (6 + 1 Test)"
batch_6 = Batch.objects.filter(title=batch_comp_title).first()
if not batch_6:
    batch_6 = Batch.objects.create(
        title=batch_comp_title,
        template=comp_template,
        sheet="sheets/stw_completion_with_test.csv",
        approver=batch_5.approver or admin_user,
        cert_no_format="SF/STW/{n:03d}/2026",
        values={
            "workshop_date": "8 October 2026",
            "venue": "Trauma Auditorium, NIMS Hospital",
            "hours": "8 hours",
            "issued": "19 October 2026"
        },
        status=Batch.Status.DRAFT,
        email_subject=mail_comp_subject,
        email_body=mail_comp_body
    )
else:
    batch_6.template = comp_template
    batch_6.sheet.name = "sheets/stw_completion_with_test.csv"
    batch_6.email_subject = mail_comp_subject
    batch_6.email_body = mail_comp_body
    batch_6.values = {
        "workshop_date": "8 October 2026",
        "venue": "Trauma Auditorium, NIMS Hospital",
        "hours": "8 hours",
        "issued": "19 October 2026"
    }
    batch_6.save()

batch_6.certificates.all().delete()
certs_comp = []
for idx, r in enumerate(completion_list, 1):
    certs_comp.append(Certificate(
        batch=batch_6,
        row=idx,
        serial=r["serial"],
        name=r["name"],
        email=r["email"],
        cert_no=f"SF/STW/{int(r['serial']):03d}/2026",
        data={"College Name": r["college"]}
    ))
# Test certificate for completion
certs_comp.append(Certificate(
    batch=batch_6,
    row=len(completion_list) + 1,
    serial="T1",
    name="SAITEST",
    email="dsaivarshini30@gmail.com",
    cert_no="SF/STW/T01-COMP/2026",
    data={"College Name": "MRECW"}
))
Certificate.objects.bulk_create(certs_comp)
print(f"Created {len(certs_comp)} certificates in Batch 6. Rendering PDFs...")
generate(batch_6)
print(f"Batch 6 completed with status: {batch_6.status}")


# =============================================================
# 3. DEDICATED SEPARATE TEST BATCH (Batch 7): SAITEST only
# =============================================================
print("\n--- Processing Dedicated Test Batch ---")
csv_test_path = Path("stw_single_test.csv")
with open(csv_test_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["Serial No", "Full Name", "Email", "College Name"])
    writer.writerow(["T1", "SAITEST", "dsaivarshini30@gmail.com", "MRECW"])

shutil.copy2(csv_test_path, media_sheets / "stw_single_test.csv")

test_batch_title = "Dedicated Test Batch - SAITEST (Single Certificate)"
batch_7 = Batch.objects.filter(title=test_batch_title).first()
if not batch_7:
    batch_7 = Batch.objects.create(
        title=test_batch_title,
        template=comp_template,
        sheet="sheets/stw_single_test.csv",
        approver=batch_5.approver or admin_user,
        cert_no_format="SF/STW/{n:03d}/2026",
        values={
            "workshop_date": "8 October 2026",
            "venue": "Trauma Auditorium, NIMS Hospital",
            "hours": "8 hours",
            "issued": "19 October 2026"
        },
        status=Batch.Status.DRAFT,
        email_subject=mail_comp_subject,
        email_body=mail_comp_body
    )
else:
    batch_7.template = comp_template
    batch_7.sheet.name = "sheets/stw_single_test.csv"
    batch_7.email_subject = mail_comp_subject
    batch_7.email_body = mail_comp_body
    batch_7.values = {
        "workshop_date": "8 October 2026",
        "venue": "Trauma Auditorium, NIMS Hospital",
        "hours": "8 hours",
        "issued": "19 October 2026"
    }
    batch_7.save()

batch_7.certificates.all().delete()
Certificate.objects.create(
    batch=batch_7,
    row=1,
    serial="T1",
    name="SAITEST",
    email="dsaivarshini30@gmail.com",
    cert_no="SF/STW/TEST-001/2026",
    data={"College Name": "MRECW"}
)
print("Rendering PDF for Dedicated Test Batch...")
generate(batch_7)
print(f"Batch 7 completed with status: {batch_7.status}")

print("\nSUCCESS! All batches, templates, and PDFs generated cleanly.")
