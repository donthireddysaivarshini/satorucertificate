"""
Batch workflow: importing sheets, generating PDFs, approval and sending.

Status flow:
  draft ──generate──> generating ──(worker)──> ready ──submit──> pending
  pending ──approve──> approved ──send──> sending ──(worker)──> sent
  pending ──reject───> rejected (editable again; regenerate and resubmit)
  pending ──withdraw─> ready;  sending ──pause──> approved;  sent ──retry failed──> sending
"""
import logging
import re
import smtplib
import time
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage, get_connection, send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import render as R
from . import sheets
from .models import Batch, Certificate, Font, Template

log = logging.getLogger("certs")
S = Batch.Status


class WorkflowError(Exception):
    pass


# ---------------------------------------------------------------- roles

def is_approver(user):
    return user.is_superuser or user.has_perm("certs.approve_batch")


def is_organiser(user):
    return user.is_superuser or user.has_perm("certs.add_batch")


def can_decide(user, batch):
    """Only the assigned approver decides, and never on their own batch."""
    return (batch.status == S.PENDING and batch.approver_id == user.pk
            and batch.created_by_id != user.pk and is_approver(user))


# ---------------------------------------------------------------- values

def batch_values(batch):
    return {**(batch.template.default_values or {}), **(batch.values or {})}


def cert_values(cert, batch=None):
    batch = batch or cert.batch
    first = cert.name.split()[0] if cert.name.split() else cert.name
    vals = {**batch_values(batch)}
    vals.update({k: v for k, v in (cert.data or {}).items() if v not in ("", None)})
    vals.update({
        "name": cert.name, "first_name": first, "email": cert.email, "cert_no": cert.cert_no,
        "serial": cert.serial, "code": cert.code, "verify_url": cert.verify_url,
        "event": batch.title, "from_name": batch.from_name or settings.ORG_NAME,
    })
    return vals


def sample_values(template):
    fmt = template.cert_no_format or "{n}"
    try:
        cert_no = fmt.format(n=1)
    except (ValueError, KeyError, IndexError):
        cert_no = "CERT/001"
    return {**(template.default_values or {}),
            "name": "Venkata Satya Sample Participant", "first_name": "Venkata",
            "email": "participant@example.com", "cert_no": cert_no, "serial": "001",
            "code": "ABCD2345XY", "verify_url": f"{settings.SITE_URL}/v/ABCD2345XY/",
            "event": template.name, "from_name": settings.ORG_NAME,
            "college": "Sample Institute of Technology, Hyderabad"}


def used_placeholders(batch):
    t = batch.template
    used = set()
    for f in t.fields or []:
        if R.is_box(f):
            used |= R.box_placeholders(f)
        else:
            used |= R.placeholders(f.get("text") or f.get("data") or "")
    used |= R.placeholders(subject_of(batch)) | R.placeholders(body_of(batch))
    return used


def subject_of(batch):
    return batch.email_subject or batch.template.email_subject


def body_of(batch):
    return batch.email_body or batch.template.email_body


def cert_format(batch):
    return batch.cert_no_format or batch.template.cert_no_format


# ---------------------------------------------------------------- import & generate

def import_sheet(batch, user=None):
    """(Re)read the sheet: replaces the batch's certificates and records problems."""
    if not batch.is_editable:
        raise WorkflowError("This batch can no longer be changed.")
    used = used_placeholders(batch)
    existing = set(Certificate.objects.exclude(batch=batch).values_list("cert_no", flat=True))
    try:
        with batch.sheet.open("rb") as fh:
            rows, errors, warnings, columns = sheets.parse(
                fh, batch.sheet.name, cert_format(batch), batch.cert_no_start, used, existing)
    except Exception as e:  # unreadable file
        rows, errors, warnings, columns = [], [f"Could not read the sheet: {e}"], [], []

    values = batch_values(batch)
    auto = {"name", "first_name", "email", "cert_no", "serial", "code", "verify_url", "event", "from_name"}
    missing = sorted(p for p in used if p not in auto and p not in values and p not in columns)
    for p in missing:
        warnings.insert(0, f"The template or email uses {{{p}}}, but it is not a sheet column or a batch value.")

    with transaction.atomic():
        delete_pdfs(batch)
        batch.certificates.all().delete()
        if not errors:
            Certificate.objects.bulk_create([
                Certificate(batch=batch, row=r["row"], name=r["name"], email=r["email"],
                            cert_no=r["cert_no"], serial=r["serial"], data=r["data"],
                            code=_unique_code())
                for r in rows
            ])
        batch.issues = {"errors": errors, "warnings": warnings, "rows": len(rows), "columns": columns}
        batch.status = S.DRAFT
        batch.save(update_fields=["issues", "status", "updated_at"])
    batch.log(user, "Sheet imported", f"{len(rows)} rows, {len(errors)} errors, {len(warnings)} warnings")
    return batch


def _unique_code():
    from .models import new_code
    while True:
        c = new_code()
        if not Certificate.objects.filter(code=c).exists():
            return c


def delete_pdfs(batch):
    for c in batch.certificates.exclude(pdf=""):
        c.pdf.delete(save=False)


def font_paths():
    return {str(f.pk): f.file.path for f in Font.objects.all() if f.file and Path(f.file.path).exists()}


def default_font_path(template):
    f = template.default_font
    return f.file.path if f and f.file and Path(f.file.path).exists() else None


def render_certificate(cert, base=None, fonts=None):
    t = cert.batch.template
    base = base or R.load_image(t.image.path)
    fonts = fonts if fonts is not None else font_paths()
    return R.render(base, t.fields or [], cert_values(cert), fonts, default_font_path(t))


def render_preview(template, fields=None, values=None, guides=False, selected=None, overrides=None):
    base = R.load_image(template.image.path)
    fields = template.fields if fields is None else fields
    img = R.render(base, fields, values or sample_values(template), font_paths(), default_font_path(template),
                   overrides=overrides)
    if guides:
        R.draw_guides(img, fields, selected)
    return img


def request_generate(batch, user):
    if batch.status not in (S.DRAFT, S.READY, S.REJECTED):
        raise WorkflowError("PDFs can only be generated while the batch is a draft.")
    if batch.errors:
        raise WorkflowError("Fix the errors in the sheet first (upload a corrected sheet).")
    if not batch.certificates.exists():
        raise WorkflowError("There are no certificates in this batch.")
    batch.status = S.GENERATING
    batch.save(update_fields=["status", "updated_at"])
    batch.log(user, "Generation started")


def generate(batch):
    """Render every PDF (run by the worker)."""
    t = batch.template
    try:
        base = R.load_image(t.image.path)
        fonts = font_paths()
        for cert in batch.certificates.all():
            img = render_certificate(cert, base, fonts)
            if cert.pdf:
                cert.pdf.delete(save=False)
            stem = re.sub(r"[^\w\-]+", "_", f"{cert.serial or cert.row}_{cert.name}").strip("_")
            cert.pdf.save(f"{stem}.pdf", ContentFile(R.to_pdf_bytes(img, t.resolution)), save=False)
            cert.email_status = Certificate.EmailStatus.NOT_SENT
            cert.save(update_fields=["pdf", "email_status"])
    except Exception as e:
        log.exception("Generation failed for batch %s", batch.pk)
        batch.status = S.DRAFT
        batch.issues = {**(batch.issues or {}), "errors": [f"Generating PDFs failed: {e}"] + batch.errors}
        batch.save(update_fields=["status", "issues", "updated_at"])
        batch.log(None, "Generation failed", str(e))
        return
    batch.status = S.READY
    batch.generated_at = timezone.now()
    batch.save(update_fields=["status", "generated_at", "updated_at"])
    batch.log(None, "PDFs generated", f"{batch.certificates.count()} certificates")


# ---------------------------------------------------------------- approval

def submit(batch, user):
    if batch.status != S.READY:
        raise WorkflowError("Generate the PDFs before submitting.")
    if not batch.approver:
        raise WorkflowError("Choose an approver first.")
    if batch.approver_id == user.pk:
        raise WorkflowError("You can't be the approver of your own batch.")
    if batch.generated_at and batch.template.updated_at > batch.generated_at:
        raise WorkflowError("The template layout changed after these PDFs were made. Generate them again.")
    batch.status = S.PENDING
    batch.submitted_at = timezone.now()
    batch.decision_comment = ""
    batch.save(update_fields=["status", "submitted_at", "decision_comment", "updated_at"])
    batch.log(user, "Submitted for approval", f"Approver: {batch.approver}")
    notify(batch.approver, f"Approval needed: {batch.title}",
           f"{user.get_full_name() or user} has submitted \"{batch.title}\" "
           f"({batch.certificates.count()} certificates) for your approval.\n\n"
           f"Review it here: {settings.SITE_URL}{batch.admin_url}\n")


def withdraw(batch, user):
    if batch.status != S.PENDING:
        raise WorkflowError("Only a batch waiting for approval can be withdrawn.")
    batch.status = S.READY
    batch.save(update_fields=["status", "updated_at"])
    batch.log(user, "Withdrawn from approval")


def approve(batch, user, comment=""):
    if not can_decide(user, batch):
        raise WorkflowError("Only the assigned approver can approve this batch.")
    batch.status = S.APPROVED
    batch.decided_at = timezone.now()
    batch.decision_comment = comment
    batch.save(update_fields=["status", "decided_at", "decision_comment", "updated_at"])
    batch.log(user, "Approved", comment)
    notify(batch.created_by, f"Approved: {batch.title}",
           f"{user.get_full_name() or user} approved \"{batch.title}\". You can now send the certificates.\n"
           + (f"\nComment: {comment}\n" if comment else "")
           + f"\n{settings.SITE_URL}{batch.admin_url}\n")


def reject(batch, user, comment):
    if not can_decide(user, batch):
        raise WorkflowError("Only the assigned approver can reject this batch.")
    if not comment.strip():
        raise WorkflowError("Please say what needs to change.")
    batch.status = S.REJECTED
    batch.decided_at = timezone.now()
    batch.decision_comment = comment
    batch.save(update_fields=["status", "decided_at", "decision_comment", "updated_at"])
    batch.log(user, "Rejected", comment)
    notify(batch.created_by, f"Changes requested: {batch.title}",
           f"{user.get_full_name() or user} rejected \"{batch.title}\":\n\n{comment}\n\n"
           f"{settings.SITE_URL}{batch.admin_url}\n")


# ---------------------------------------------------------------- sending

def start_sending(batch, user):
    if batch.status != S.APPROVED:
        raise WorkflowError("Only an approved batch can be sent.")
    n = batch.certificates.filter(revoked=False).exclude(email_status=Certificate.EmailStatus.SENT).update(
        email_status=Certificate.EmailStatus.PENDING, email_error="")
    batch.status = S.SENDING
    batch.save(update_fields=["status", "updated_at"])
    batch.log(user, "Sending started", f"{n} emails queued")


def pause_sending(batch, user):
    if batch.status != S.SENDING:
        raise WorkflowError("This batch isn't sending.")
    batch.status = S.APPROVED
    batch.save(update_fields=["status", "updated_at"])
    batch.log(user, "Sending paused")


def retry_failed(batch, user):
    if batch.status not in (S.SENT, S.APPROVED):
        raise WorkflowError("Wait for sending to finish first.")
    n = batch.certificates.filter(email_status=Certificate.EmailStatus.FAILED, revoked=False).update(
        email_status=Certificate.EmailStatus.PENDING, email_error="")
    if not n:
        raise WorkflowError("No failed emails to retry.")
    batch.status = S.SENDING
    batch.save(update_fields=["status", "updated_at"])
    batch.log(user, "Retrying failed emails", f"{n} queued")


def build_email(cert, to=None, test=False):
    batch = cert.batch
    vals = cert_values(cert, batch)
    subject = R.fill(subject_of(batch), vals)
    from_name = batch.from_name or settings.ORG_NAME
    from_addr = settings.DEFAULT_FROM_EMAIL
    msg = EmailMessage(
        subject=f"[TEST → {cert.email}] {subject}" if test else subject,
        body=R.fill(body_of(batch), vals),
        from_email=f"{from_name} <{from_addr}>" if from_name else from_addr,
        to=[to or cert.email],
        reply_to=[batch.reply_to] if batch.reply_to else None,
    )
    with cert.pdf.open("rb") as fh:
        msg.attach(cert.filename or "certificate.pdf", fh.read(), "application/pdf")
    return msg


def send_test(batch, user):
    cert = batch.certificates.exclude(pdf="").first()
    if not cert:
        raise WorkflowError("Generate the PDFs first.")
    if not user.email:
        raise WorkflowError("Add an email address to your user profile first.")
    build_email(cert, to=user.email, test=True).send()
    batch.log(user, "Test email sent", f"to {user.email}")
    return user.email


def send_pending(batch, delay=None, sleep=time.sleep):
    """Send every queued email in a batch (run by the worker). Stops if the batch is paused."""
    delay = settings.EMAIL_SEND_DELAY if delay is None else delay
    E = Certificate.EmailStatus
    conn = None
    sent = failed = 0
    try:
        while True:
            if not Batch.objects.filter(pk=batch.pk, status=S.SENDING).exists():
                batch.log(None, "Sending stopped", f"{sent} sent, {failed} failed this run")
                return
            cert = batch.certificates.filter(email_status=E.PENDING, revoked=False).order_by("row").first()
            if not cert:
                break
            msg = build_email(cert)
            error = ""
            for attempt in (1, 2, 3):
                try:
                    if conn is None:
                        conn = get_connection()
                        conn.open()
                    msg.connection = conn
                    msg.send()
                    error = ""
                    break
                except smtplib.SMTPRecipientsRefused as e:
                    error = f"Recipient refused: {e.recipients}"
                    break
                except smtplib.SMTPAuthenticationError as e:
                    error = f"SMTP login failed: {e}"
                    break
                except Exception as e:  # dropped connection, timeout, server busy…
                    error = str(e) or e.__class__.__name__
                    try:
                        conn and conn.close()
                    except Exception:
                        pass
                    conn = None
                    if attempt < 3:
                        sleep(10 * attempt)
            if error:
                cert.email_status, cert.email_error = E.FAILED, error[:2000]
                failed += 1
                if "login failed" in error:
                    cert.save(update_fields=["email_status", "email_error"])
                    Batch.objects.filter(pk=batch.pk).update(status=S.APPROVED)
                    batch.log(None, "Sending stopped", error)
                    return
            else:
                cert.email_status, cert.email_error, cert.sent_at = E.SENT, "", timezone.now()
                sent += 1
            cert.save(update_fields=["email_status", "email_error", "sent_at"])
            if delay:
                sleep(delay)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    Batch.objects.filter(pk=batch.pk, status=S.SENDING).update(status=S.SENT, updated_at=timezone.now())
    batch.refresh_from_db()
    totals = batch.certificates.filter(email_status=E.FAILED).count()
    batch.log(None, "Sending finished", f"{sent} sent, {failed} failed this run; {totals} failed in total")
    if batch.created_by:
        notify(batch.created_by, f"Sent: {batch.title}",
               f"Finished sending \"{batch.title}\": {sent} sent, {failed} failed.\n\n"
               f"{settings.SITE_URL}{batch.admin_url}\n")


def resend(cert, user):
    if not cert.is_issued:
        raise WorkflowError("This certificate's batch isn't approved.")
    if cert.revoked:
        raise WorkflowError("This certificate is revoked.")
    build_email(cert).send()
    cert.email_status, cert.email_error, cert.sent_at = Certificate.EmailStatus.SENT, "", timezone.now()
    cert.save(update_fields=["email_status", "email_error", "sent_at"])
    cert.batch.log(user, "Email resent", f"{cert.cert_no} to {cert.email}", certificate=cert)


def set_revoked(cert, user, revoked, reason=""):
    cert.revoked = revoked
    cert.revoked_reason = reason if revoked else ""
    cert.revoked_at = timezone.now() if revoked else None
    cert.save(update_fields=["revoked", "revoked_reason", "revoked_at"])
    cert.batch.log(user, "Certificate revoked" if revoked else "Certificate reinstated",
                   f"{cert.cert_no}" + (f": {reason}" if reason else ""), certificate=cert)


# ---------------------------------------------------------------- notifications

def notify(user, subject, body):
    if not user or not user.email:
        return
    try:
        send_mail(f"[{settings.ORG_NAME}] {subject}", body, settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:
        log.exception("Could not send notification to %s", user.email)
