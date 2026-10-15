import io
import json
import shutil
import smtplib
import tempfile
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from . import services as svc
from .models import Batch, Certificate, Template

TMP = tempfile.mkdtemp()


def png_bytes(w=2000, h=1414):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


SHEET = (
    "Name,Email ID,College Name,Certificate No\n"
    "Asha Rao,asha@example.com,JNTU Hyderabad,1\n"
    "Venkata Satya Narayana Murthy,v@example.com,Sreenidhi Institute of Science and Technology,2\n"
    "Meena S,meena@example.com,Osmania University,SF/STW/003/2026\n"
)

# Layout as saved by the layout editor (boxes)
FIELDS = [
    {"key": "name", "label": "Name", "source": "column", "column": "Name", "x": 885, "y": 520, "w": 860, "h": 102,
     "align": "center", "size": 64, "weight": "SemiBold", "color": "#3d3b3a"},
    {"key": "college", "label": "College", "source": "column", "column": "College", "x": 382, "y": 1025, "w": 698,
     "h": 45, "align": "left", "size": 28, "weight": "Regular", "color": "#3d3b3a"},
    {"key": "date", "label": "Workshop Date", "source": "pattern", "pattern": "{workshop_date}", "x": 396, "y": 965,
     "w": 520, "h": 45, "align": "left", "size": 28, "weight": "Regular", "color": "#3d3b3a"},
    {"key": "cert_no", "label": "Certificate No", "source": "pattern", "pattern": "Certificate No : {cert_no}",
     "x": 173, "y": 1209, "w": 527, "h": 38, "align": "left", "size": 28, "weight": "Regular", "color": "#3d3b3a",
     "fill": "#ffffff"},
    {"key": "qr", "label": "QR code", "source": "qr", "x": 1650, "y": 1050, "w": 220, "h": 220, "align": "center",
     "size": 28, "weight": "Regular", "color": "#000000"},
]
# Older anchor-point layout (still rendered)
LEGACY_FIELDS = [
    {"id": "f1", "type": "text", "text": "{name}", "x": 1315, "y": 598, "anchor": "ms", "size": 64, "max_width": 1000},
    {"id": "f3", "type": "text", "text": "Certificate No : {cert_no}", "x": 175, "y": 1237,
     "cover": [173, 1209, 700, 1247]},
    {"id": "f4", "type": "qr", "x": 1650, "y": 1050, "size": 220},
]


@override_settings(MEDIA_ROOT=TMP, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   EMAIL_SEND_DELAY=0, SITE_URL="https://certs.example.org")
class WorkflowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        call_command("init_roles", stdout=io.StringIO())
        self.org = User.objects.create_user("org", "org@example.com", "pw", is_staff=True)
        self.org.groups.add(Group.objects.get(name="Organisers"))
        self.appr = User.objects.create_user("appr", "appr@example.com", "pw", is_staff=True)
        self.appr.groups.add(Group.objects.get(name="Approvers"))
        self.other = User.objects.create_user("other", "other@example.com", "pw", is_staff=True)
        self.other.groups.add(Group.objects.get(name="Approvers"))
        self.t = Template.objects.create(
            name="STW", image=SimpleUploadedFile("t.png", png_bytes(), "image/png"), fields=FIELDS,
            default_values={"workshop_date": "8 September 2026"}, cert_no_format="SF/STW/{n:03d}/2026")

    def make_batch(self, sheet=SHEET, name="p.csv"):
        self.client.force_login(self.org)
        r = self.client.post(reverse("admin:certs_batch_add"), {
            "title": "STW Workshop Sep 2026", "template": self.t.pk, "approver": self.appr.pk,
            "sheet": SimpleUploadedFile(name, sheet.encode(), "text/csv"),
            "values_text": "hours = 8 hours", "cert_no_format": "", "email_subject": "", "email_body": "",
            "from_name": "SF Team", "reply_to": "",
            "certificates-TOTAL_FORMS": 0, "certificates-INITIAL_FORMS": 0,
            "logs-TOTAL_FORMS": 0, "logs-INITIAL_FORMS": 0, "_save": "Save"})
        self.assertEqual(r.status_code, 302, r.content[:2000])
        return Batch.objects.latest("pk")

    def act(self, user, batch, action, **data):
        self.client.force_login(user)
        return self.client.post(reverse("admin:certs_batch_action", args=[batch.pk, action]), data)

    def test_full_flow(self):
        b = self.make_batch()
        self.assertEqual(b.status, "draft")
        self.assertEqual(b.errors, [])
        certs = list(b.certificates.order_by("row"))
        self.assertEqual([c.cert_no for c in certs], ["SF/STW/001/2026", "SF/STW/002/2026", "SF/STW/003/2026"])
        self.assertEqual(certs[2].serial, "003")
        self.assertEqual(certs[0].data["college"], "JNTU Hyderabad")  # {college} filled from "College Name"
        self.assertEqual(b.warnings, [])

        # can't submit before generating
        self.act(self.org, b, "submit")
        b.refresh_from_db()
        self.assertEqual(b.status, "draft")

        self.act(self.org, b, "generate")
        b.refresh_from_db()
        self.assertEqual(b.status, "generating")
        call_command("worker", once=True, stdout=io.StringIO())
        b.refresh_from_db()
        self.assertEqual(b.status, "ready")
        self.assertTrue(all(c.pdf for c in b.certificates.all()))

        # organiser can't approve; wrong approver can't approve
        self.act(self.org, b, "submit")
        b.refresh_from_db()
        self.assertEqual(b.status, "pending")
        self.assertIn("Approval needed", mail.outbox[-1].subject)
        self.assertEqual(mail.outbox[-1].to, ["appr@example.com"])
        self.act(self.org, b, "approve")
        self.act(self.other, b, "approve")
        b.refresh_from_db()
        self.assertEqual(b.status, "pending")

        # the batch is hidden from approvers it isn't assigned to
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("admin:certs_batch_change", args=[b.pk])).status_code, 302)
        self.client.force_login(self.appr)
        r = self.client.get(reverse("admin:certs_batch_change", args=[b.pk]))
        self.assertContains(r, "Approve")

        # reject needs a comment
        self.act(self.appr, b, "reject", comment="")
        b.refresh_from_db()
        self.assertEqual(b.status, "pending")
        self.act(self.appr, b, "reject", comment="Fix the date")
        b.refresh_from_db()
        self.assertEqual(b.status, "rejected")

        # regenerate, resubmit, approve
        self.act(self.org, b, "generate")
        call_command("worker", once=True, stdout=io.StringIO())
        self.act(self.org, b, "submit")
        self.act(self.appr, b, "approve", comment="Looks good")
        b.refresh_from_db()
        self.assertEqual(b.status, "approved")

        # locked after approval
        self.client.force_login(self.org)
        r = self.client.post(reverse("admin:certs_batch_change", args=[b.pk]), {"title": "changed"})
        b.refresh_from_db()
        self.assertEqual(b.title, "STW Workshop Sep 2026")

        mail.outbox.clear()
        self.act(self.org, b, "send")
        call_command("worker", once=True, stdout=io.StringIO())
        b.refresh_from_db()
        self.assertEqual(b.status, "sent")
        cert_mails = [m for m in mail.outbox if m.attachments]
        self.assertEqual(len(cert_mails), 3)
        m = cert_mails[0]
        self.assertEqual(m.to, ["asha@example.com"])
        self.assertIn("SF/STW/001/2026", m.body)
        self.assertIn("https://certs.example.org/v/", m.body)
        self.assertEqual(m.attachments[0][2], "application/pdf")
        self.assertEqual(b.certificates.filter(email_status="sent").count(), 3)

        # public verification
        c = b.certificates.get(row=2)
        r = self.client.get(reverse("verify_code", args=[c.code]))
        self.assertContains(r, "Valid certificate")
        self.assertContains(r, "Asha Rao")
        self.assertContains(r, "8 September 2026")
        r = self.client.get(reverse("verify"), {"q": c.cert_no})
        self.assertContains(r, "also enter the name")
        r = self.client.get(reverse("verify"), {"q": c.cert_no, "name": "asha  rao"})
        self.assertRedirects(r, reverse("verify_code", args=[c.code]))
        r = self.client.get(reverse("verify"), {"q": c.cert_no, "name": "Someone Else"})
        self.assertContains(r, "No certificate matches")

        # revoke
        svc.set_revoked(c, self.org, True, "Issued with wrong name")
        r = self.client.get(reverse("verify_code", args=[c.code]))
        self.assertContains(r, "revoked")
        self.assertContains(r, "Issued with wrong name")

    def test_sheet_problems(self):
        sheet = ("Name,Email,Certificate No\n"
                 "A,a@example.com,1\nB,not-an-email,2\nC,c@example.com,1\n,,\n")
        b = self.make_batch(sheet)
        self.assertEqual(b.certificates.count(), 0)
        joined = " ".join(b.errors)
        self.assertIn("invalid email", joined)
        self.assertIn("also on row 2", joined)
        self.act(self.org, b, "generate")
        b.refresh_from_db()
        self.assertEqual(b.status, "draft")

    def test_cert_no_already_used(self):
        b1 = self.make_batch()
        b2 = self.make_batch()
        self.assertTrue(any("already issued" in e for e in b2.errors))

    def test_auto_numbering_xlsx_and_missing_placeholder(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(["Full Name", "E-mail"])
        wb.active.append(["X Y", "x@example.com"])
        buf = io.BytesIO()
        wb.save(buf)
        self.client.force_login(self.org)
        self.client.post(reverse("admin:certs_batch_add"), {
            "title": "Auto", "template": self.t.pk, "approver": self.appr.pk, "cert_no_start": 41,
            "sheet": SimpleUploadedFile("s.xlsx", buf.getvalue()), "values_text": "",
            "certificates-TOTAL_FORMS": 0, "certificates-INITIAL_FORMS": 0,
            "logs-TOTAL_FORMS": 0, "logs-INITIAL_FORMS": 0})
        b = Batch.objects.get(title="Auto")
        self.assertEqual(b.certificates.get().cert_no, "SF/STW/041/2026")
        self.assertTrue(any("{college}" in w for w in b.warnings))

    def test_cannot_pick_self_as_approver(self):
        b = self.make_batch()
        b.approver = self.org
        b.save()
        self.act(self.org, b, "generate")
        call_command("worker", once=True, stdout=io.StringIO())
        self.act(self.org, b, "submit")
        b.refresh_from_db()
        self.assertEqual(b.status, "ready")

    def test_send_failures_are_recorded_and_retried(self):
        b = self.make_batch()
        svc.request_generate(b, self.org)
        svc.generate(b)
        b.refresh_from_db()
        svc.submit(b, self.org)
        svc.approve(b, self.appr)
        svc.start_sending(b, self.org)

        real_send = svc.EmailMessage.send

        def flaky(msg, *a, **k):
            if msg.to == ["v@example.com"]:
                raise smtplib.SMTPRecipientsRefused({"v@example.com": (550, b"no such user")})
            return real_send(msg, *a, **k)

        with mock.patch.object(svc.EmailMessage, "send", flaky):
            svc.send_pending(b, delay=0, sleep=lambda s: None)
        b.refresh_from_db()
        self.assertEqual(b.status, "sent")
        self.assertEqual(b.certificates.get(email="v@example.com").email_status, "failed")
        svc.retry_failed(b, self.org)
        svc.send_pending(b, delay=0, sleep=lambda s: None)
        self.assertEqual(b.certificates.filter(email_status="sent").count(), 3)

    def test_editor_layout_and_preview(self):
        self.client.force_login(self.org)
        r = self.client.get(reverse("admin:certs_template_editor", args=[self.t.pk]))
        self.assertContains(r, "editor-config")
        self.assertContains(r, "fabric-5.3.0.min.js")
        self.assertIn("csrftoken", r.cookies)
        # load
        r = self.client.get(reverse("admin:certs_template_layout", args=[self.t.pk]))
        data = r.json()
        self.assertEqual(data["template_size"], [2000, 1414])
        self.assertEqual(len(data["fields"]), 5)
        # preview in the editor's format
        r = self.client.post(reverse("admin:certs_template_preview", args=[self.t.pk]),
                             json.dumps({"layout": data, "values": {"name": "A Very Long Example Name"}}),
                             content_type="application/json")
        self.assertEqual(r["Content-Type"], "image/jpeg")
        # save
        r = self.client.post(reverse("admin:certs_template_layout", args=[self.t.pk]),
                             json.dumps({"version": 1, "fields": FIELDS[:2]}), content_type="application/json")
        self.assertEqual(r.json()["ok"], True)
        self.t.refresh_from_db()
        self.assertEqual([f["key"] for f in self.t.fields], ["name", "college"])
        r = self.client.post(reverse("admin:certs_template_layout", args=[self.t.pk]), "nonsense",
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        # approvers can look but not save
        self.client.force_login(self.appr)
        self.assertEqual(self.client.get(reverse("admin:certs_template_editor", args=[self.t.pk])).status_code, 200)
        r = self.client.post(reverse("admin:certs_template_layout", args=[self.t.pk]),
                             json.dumps({"fields": []}), content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_legacy_layout_still_renders_and_converts(self):
        from . import render as R
        self.t.fields = LEGACY_FIELDS
        self.t.save()
        b = self.make_batch()
        svc.request_generate(b, self.org)
        svc.generate(b)
        b.refresh_from_db()
        self.assertEqual(b.status, "ready")
        boxes = [R.legacy_to_box(f, i) for i, f in enumerate(LEGACY_FIELDS, 1)]
        self.assertEqual(boxes[0]["align"], "center")
        self.assertEqual(boxes[0]["x"] + boxes[0]["w"] // 2, 1315)
        self.assertEqual(boxes[1]["fill"], "#ffffff")
        self.assertEqual((boxes[1]["x"], boxes[1]["y"], boxes[1]["w"]), (173, 1209, 527))
        self.assertEqual(boxes[2]["source"], "qr")

    def test_box_rendering(self):
        from . import render as R
        img = Image.new("RGB", (2000, 1414), "white")
        f = dict(FIELDS[0])
        out = R.render(img, [f], {"name": "X" * 200})  # far too long: shrinks, never below 14
        self.assertNotEqual(out.getbbox(), None)
        # pattern with a spaced column heading and a batch value
        self.assertEqual(R.fill_pattern("SF/{Certificate No}/{workshop_date}/{missing}",
                                        {"certificate_no": "7", "workshop_date": "8 Sep"}), "SF/7/8 Sep/{missing}")
        self.assertEqual(R.box_placeholders({"source": "column", "column": "College Name"}), {"college_name"})

    def test_files_need_login(self):
        b = self.make_batch()
        self.client.logout()
        r = self.client.get(reverse("protected_file", args=[b.sheet.name]))
        self.assertEqual(r.status_code, 302)
        self.client.force_login(self.org)
        r = self.client.get(reverse("protected_file", args=["../../etc/passwd"]))
        self.assertEqual(r.status_code, 404)
