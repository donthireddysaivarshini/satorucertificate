import secrets
from pathlib import Path

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion


def new_code(length=10):
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def parse_values(text):
    """'key = value' lines -> dict (blank lines and # comments ignored)."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().lower().replace(" ", "_")
        if k:
            out[k] = v.strip()
    return out


def format_values(d):
    return "\n".join(f"{k} = {v}" for k, v in (d or {}).items())


def default_cert_format():
    return "CERT/{n:03d}/" + str(timezone.now().year)


class Font(models.Model):
    name = models.CharField(max_length=100, unique=True)
    file = models.FileField(upload_to="fonts/", help_text="A .ttf or .otf file. Variable fonts support weights (Regular, SemiBold, Bold…).")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


DEFAULT_SUBJECT = "Your certificate – {event}"
DEFAULT_BODY = """Dear {name},

Thank you for participating in {event}.

Please find your certificate attached to this email.
Certificate No: {cert_no}
You can verify it at any time: {verify_url}

Warm regards,
{from_name}
"""


class Template(models.Model):
    """A certificate design: background image + where each piece of text / QR code goes."""
    name = models.CharField(max_length=200, unique=True)
    image = models.ImageField(upload_to="templates/", help_text="Blank certificate design (PNG or JPG), about 2000 px wide.")
    default_font = models.ForeignKey(Font, null=True, blank=True, on_delete=models.SET_NULL,
                                     help_text="Used by fields that don't pick their own font.")
    fields = models.JSONField(default=list, blank=True, editable=False,
                              help_text="Layout; edited with the layout editor.")
    default_values = models.JSONField(default=dict, blank=True,
                                      help_text="Fixed text copied into each new batch.")
    cert_no_format = models.CharField(max_length=100, default=default_cert_format,
                                      help_text="How numbers become certificate numbers. {n:03d} = 001, 002…")
    email_subject = models.CharField(max_length=300, default=DEFAULT_SUBJECT)
    email_body = models.TextField(default=DEFAULT_BODY)
    resolution = models.PositiveIntegerField(default=150, help_text="PDF page size = image size ÷ dpi. For A4 landscape use image width ÷ 11.69 (2000 px → 171).")
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Batch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GENERATING = "generating", "Generating PDFs"
        READY = "ready", "Ready to submit"
        PENDING = "pending", "Waiting for approval"
        REJECTED = "rejected", "Rejected"
        APPROVED = "approved", "Approved"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"

    EDITABLE = {Status.DRAFT, Status.READY, Status.REJECTED}
    ISSUED = {Status.APPROVED, Status.SENDING, Status.SENT}

    title = models.CharField(max_length=200, help_text="Shown in emails as {event} and on the verify page.")
    template = models.ForeignKey(Template, on_delete=models.PROTECT, related_name="batches")
    sheet = models.FileField(upload_to="sheets/", help_text=".xlsx or .csv with a header row. Needs Name and Email columns; Certificate No is optional.")
    values = models.JSONField(default=dict, blank=True, help_text="Fixed text for this batch, e.g. dates.")
    cert_no_format = models.CharField(max_length=100, blank=True)
    cert_no_start = models.PositiveIntegerField(null=True, blank=True,
                                                help_text="Auto-number from this value when the sheet has no Certificate No column.")
    email_subject = models.CharField(max_length=300, blank=True)
    email_body = models.TextField(blank=True)
    from_name = models.CharField(max_length=200, blank=True)
    reply_to = models.EmailField(blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    issues = models.JSONField(default=dict, blank=True, editable=False)  # {"errors": [...], "warnings": [...]}
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name="batches_created", editable=False)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="batches_to_approve")
    decision_comment = models.TextField(blank=True, editable=False)
    generated_at = models.DateTimeField(null=True, blank=True, editable=False)
    submitted_at = models.DateTimeField(null=True, blank=True, editable=False)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "batches"
        permissions = [("approve_batch", "Can approve or reject batches")]

    def __str__(self):
        return self.title

    @property
    def is_editable(self):
        return self.status in self.EDITABLE

    @property
    def errors(self):
        return (self.issues or {}).get("errors", [])

    @property
    def warnings(self):
        return (self.issues or {}).get("warnings", [])

    @property
    def admin_url(self):
        return reverse("admin:certs_batch_change", args=[self.pk])

    def log(self, user, action, comment="", certificate=None):
        return BatchLog.objects.create(batch=self, user=user, action=action, comment=comment, certificate=certificate)


def pdf_path(instance, filename):
    return f"certificates/{instance.batch_id}/{filename}"


class Certificate(models.Model):
    class EmailStatus(models.TextChoices):
        NOT_SENT = "not_sent", "Not sent"
        PENDING = "pending", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="certificates")
    row = models.PositiveIntegerField(help_text="Row number in the uploaded sheet.")
    name = models.CharField(max_length=300)
    email = models.EmailField(max_length=254, blank=True)
    cert_no = models.CharField("certificate no", max_length=100, unique=True)
    serial = models.CharField(max_length=20, blank=True)
    code = models.CharField("verification code", max_length=16, unique=True, default=new_code, editable=False)
    data = models.JSONField(default=dict, blank=True, help_text="All other columns from the sheet.")
    pdf = models.FileField(upload_to=pdf_path, blank=True)

    email_status = models.CharField(max_length=20, choices=EmailStatus.choices, default=EmailStatus.NOT_SENT)
    email_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    revoked = models.BooleanField(default=False)
    revoked_reason = models.CharField(max_length=300, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["batch", "row"]

    def __str__(self):
        return f"{self.cert_no} – {self.name}"

    @property
    def verify_url(self):
        return f"{settings.SITE_URL}{reverse('verify_code', args=[self.code])}"

    @property
    def is_issued(self):
        return self.batch.status in Batch.ISSUED

    @property
    def filename(self):
        return Path(self.pdf.name).name if self.pdf else ""


class BatchLog(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="logs")
    certificate = models.ForeignKey(Certificate, null=True, blank=True, on_delete=models.SET_NULL)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "history entry"
        verbose_name_plural = "history"

    def __str__(self):
        return f"{self.action} by {self.user or 'system'}"


# Remove generated PDFs from disk when their certificate rows are deleted.
from django.db.models.signals import post_delete  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_delete, sender=Certificate)
def _delete_pdf(sender, instance, **kwargs):
    if instance.pdf:
        instance.pdf.delete(save=False)


@receiver(post_delete, sender=Batch)
def _delete_sheet(sender, instance, **kwargs):
    if instance.sheet:
        instance.sheet.delete(save=False)
