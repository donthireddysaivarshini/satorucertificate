import json

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.middleware.csrf import get_token
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from django.utils.html import format_html, format_html_join

from . import render as R
from . import services as svc
from .models import Batch, BatchLog, Certificate, Font, Template, format_values, parse_values

admin.site.site_header = f"{settings.ORG_NAME} · Certificates"
admin.site.site_title = settings.ORG_NAME
admin.site.index_title = "Certificates"

S = Batch.Status
STATUS_COLOURS = {
    S.DRAFT: "#64748b", S.GENERATING: "#7c3aed", S.READY: "#0369a1", S.PENDING: "#b45309",
    S.REJECTED: "#b91c1c", S.APPROVED: "#15803d", S.SENDING: "#7c3aed", S.SENT: "#166534",
}
EMAIL_COLOURS = {"not_sent": "#64748b", "pending": "#7c3aed", "sent": "#15803d", "failed": "#b91c1c"}


def badge(text, colour):
    return format_html('<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
                       'background:{}1a;color:{};font-weight:600;font-size:11px;white-space:nowrap">{}</span>',
                       colour, colour, text)


def approver_only(user):
    return not user.is_superuser and svc.is_approver(user) and not svc.is_organiser(user)


def file_link(f, label="Open"):
    if not f:
        return "—"
    return format_html('<a href="{}" target="_blank">{}</a>', reverse("protected_file", args=[f.name]), label)


# ---------------------------------------------------------------- fonts

@admin.register(Font)
class FontAdmin(admin.ModelAdmin):
    list_display = ["name", "file"]


# ---------------------------------------------------------------- templates

class TemplateForm(forms.ModelForm):
    default_values_text = forms.CharField(
        label="Default values", required=False,
        widget=forms.Textarea(attrs={"rows": 4, "style": "font-family:monospace;width:40em"}),
        help_text="One per line, like <code>workshop_date = 8 September 2026</code>. "
                  "Use them in the layout as {workshop_date}. Batches can override them.")

    class Meta:
        model = Template
        fields = ["name", "image", "default_font", "cert_no_format", "resolution",
                  "email_subject", "email_body", "notes"]
        widgets = {"email_body": forms.Textarea(attrs={"rows": 12, "style": "width:40em"}),
                   "email_subject": forms.TextInput(attrs={"style": "width:40em"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk:
            self.fields["default_values_text"].initial = format_values(self.instance.default_values)

    def save(self, commit=True):
        self.instance.default_values = parse_values(self.cleaned_data.get("default_values_text"))
        return super().save(commit)


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    form = TemplateForm
    list_display = ["name", "field_count", "batch_count", "updated_at", "editor_link"]
    search_fields = ["name"]
    change_form_template = "admin/certs/template/change_form.html"
    fieldsets = [
        (None, {"fields": ["name", "image", "default_font", "default_values_text", "cert_no_format", "resolution"]}),
        ("Default email (each batch can change it)", {"fields": ["email_subject", "email_body"]}),
        ("Notes", {"fields": ["notes"], "classes": ["collapse"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(n_batches=Count("batches"))

    @admin.display(description="Fields")
    def field_count(self, obj):
        return len(obj.fields or [])

    @admin.display(description="Batches", ordering="n_batches")
    def batch_count(self, obj):
        return obj.n_batches

    @admin.display(description="")
    def editor_link(self, obj):
        return format_html('<a href="{}">Layout editor →</a>', reverse("admin:certs_template_editor", args=[obj.pk]))

    def get_urls(self):
        return [
            path("<int:pk>/editor/", self.admin_site.admin_view(self.editor_view), name="certs_template_editor"),
            path("<int:pk>/layout.json", self.admin_site.admin_view(self.layout_view), name="certs_template_layout"),
            path("<int:pk>/preview/", self.admin_site.admin_view(self.preview_view), name="certs_template_preview"),
        ] + super().get_urls()

    def response_add(self, request, obj, post_url_continue=None):
        if "_continue" not in request.POST and "_addanother" not in request.POST:
            messages.info(request, "Now place the text on your template.")
            return redirect("admin:certs_template_editor", obj.pk)
        return super().response_add(request, obj, post_url_continue)

    def _template_for(self, request, pk):
        t = get_object_or_404(Template, pk=pk)
        if not self.has_view_permission(request, t):
            raise PermissionDenied
        return t

    def editor_view(self, request, pk):
        t = self._template_for(request, pk)
        get_token(request)  # the editor reads the CSRF cookie
        font = t.default_font
        url = lambda name: reverse(f"admin:certs_template_{name}", args=[t.pk])
        config = {
            "templateUrl": reverse("protected_file", args=[t.image.name]),
            "templateName": t.image.name.split("/")[-1],
            "layoutUrl": url("layout"), "saveUrl": url("layout"), "previewUrl": url("preview"),
            "backUrl": url("change"),
            "fontUrl": reverse("protected_file", args=[font.file.name]) if font and font.file else "",
            "canEdit": self.has_change_permission(request, t),
            "title": t.name,
            "sample": {k: v for k, v in svc.sample_values(t).items() if k != "verify_url"},
        }
        # JSON that is safe inside <script>: no "</script>" or HTML specials can break out.
        raw = json.dumps(config).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        ctx = {"editor_config_json": mark_safe(raw)}
        return render(request, "certs/layout_editor.html", ctx)

    def layout_view(self, request, pk):
        t = self._template_for(request, pk)
        if request.method == "POST":
            if not self.has_change_permission(request, t):
                raise PermissionDenied
            try:
                data = json.loads(request.body)
                fields = data["fields"]
                assert isinstance(fields, list)
                t.fields = clean_fields(fields)
            except Exception:
                return JsonResponse({"ok": False, "error": "That isn't a certificate layout."}, status=400)
            t.save()
            return JsonResponse({"ok": True, "fields": len(t.fields)})
        if not t.fields:
            return JsonResponse({"error": "No layout yet."}, status=404)
        from PIL import Image
        with Image.open(t.image.path) as im:
            size = list(im.size)
        boxes = [R.legacy_to_box(f, i) for i, f in enumerate(t.fields, 1)]
        return JsonResponse({"version": 1, "template": t.image.name.split("/")[-1],
                             "template_size": size, "fields": boxes})

    def preview_view(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        t = self._template_for(request, pk)
        body = json.loads(request.body or "{}")
        if "layout" in body:  # layout editor: {"layout": {...}, "values": {field key: text}}
            fields = clean_fields((body.get("layout") or {}).get("fields", []))
            sample = svc.sample_values(t)
            # Show the editor's example text; fields without one are filled from sample data.
            overrides = {}
            for f in fields:
                v = (body.get("values") or {}).get(f.get("key"))
                if f.get("source") == "fixed" or (f.get("sample") and v):
                    overrides[f["key"]] = R.fill_pattern(v, sample) if v else v
            img = svc.render_preview(t, fields, sample, overrides=overrides)
            return HttpResponse(R.to_jpeg_bytes(img, max_width=img.width, quality=90), content_type="image/jpeg")
        fields = clean_fields(body.get("fields", t.fields or []))
        values = {**svc.sample_values(t), **{k: str(v) for k, v in (body.get("sample") or {}).items()}}
        img = svc.render_preview(t, fields, values, guides=body.get("guides", True), selected=body.get("selected"))
        return HttpResponse(R.to_jpeg_bytes(img, max_width=int(body.get("width") or 1400)), content_type="image/jpeg")


FIELD_KEYS = {
    # layout editor boxes
    "key", "label", "source", "column", "pattern", "sample", "w", "h", "align", "fill",
    # older anchor-point fields
    "id", "type", "text", "data", "x", "y", "anchor", "size", "weight", "font", "color",
    "max_width", "min_size", "case", "cover", "cover_color", "background",
}
INT_KEYS = ("x", "y", "w", "h", "size", "max_width", "min_size")


def clean_fields(fields):
    out = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        f = {k: v for k, v in f.items() if k in FIELD_KEYS and v not in ("", None)}
        try:
            for k in INT_KEYS:
                if k in f:
                    f[k] = int(round(float(f[k])))
        except (TypeError, ValueError):
            continue
        if "x" not in f or "y" not in f:
            continue
        if "cover" in f and not (isinstance(f["cover"], list) and len(f["cover"]) == 4):
            f.pop("cover")
        if R.is_box(f):
            f["w"], f["h"] = max(10, f["w"]), max(10, f["h"])
            if f.get("source") not in ("column", "fixed", "pattern", "qr"):
                f["source"] = "fixed"
            f.setdefault("text", "") if f["source"] == "fixed" else None
        else:
            f.setdefault("type", "text")
        out.append(f)
    return out


# ---------------------------------------------------------------- batches

class BatchForm(forms.ModelForm):
    values_text = forms.CharField(
        label="Values for this batch", required=False,
        widget=forms.Textarea(attrs={"rows": 4, "style": "font-family:monospace;width:40em"}),
        help_text="One per line, like <code>workshop_date = 8 September 2026</code>. "
                  "Anything left out uses the template's default values.")

    class Meta:
        model = Batch
        fields = ["title", "template", "sheet", "approver", "cert_no_format", "cert_no_start",
                  "email_subject", "email_body", "from_name", "reply_to"]
        widgets = {"email_body": forms.Textarea(attrs={"rows": 10, "style": "width:40em",
                                                       "placeholder": "Leave blank to use the template's email"}),
                   "email_subject": forms.TextInput(attrs={"style": "width:40em",
                                                           "placeholder": "Leave blank to use the template's subject"}),
                   "cert_no_format": forms.TextInput(attrs={"placeholder": "Leave blank to use the template's format"})}
        labels = {"cert_no_start": "Number from"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk and "values_text" in self.fields:
            self.fields["values_text"].initial = format_values(self.instance.values)

    def clean_sheet(self):
        f = self.cleaned_data.get("sheet")
        if f and not str(f.name).lower().endswith((".xlsx", ".xlsm", ".csv")):
            raise forms.ValidationError("Upload an .xlsx or .csv file.")
        return f

    def save(self, commit=True):
        if "values_text" in self.cleaned_data:
            self.instance.values = parse_values(self.cleaned_data.get("values_text"))
        return super().save(commit)


class CertificateInline(admin.TabularInline):
    model = Certificate
    fields = ["row", "name", "email", "cert_link", "pdf_link", "email_badge", "valid"]
    readonly_fields = fields
    extra = 0
    max_num = 0
    can_delete = False
    verbose_name_plural = "Certificates"

    @admin.display(description="Certificate no")
    def cert_link(self, obj):
        return format_html('<a href="{}">{}</a>', reverse("admin:certs_certificate_change", args=[obj.pk]), obj.cert_no)

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        return file_link(obj.pdf, "View")

    @admin.display(description="Email")
    def email_badge(self, obj):
        b = badge(obj.get_email_status_display(), EMAIL_COLOURS.get(obj.email_status, "#64748b"))
        if obj.email_error:
            return format_html('{}<div style="color:#b91c1c;font-size:11px;max-width:18em">{}</div>', b, obj.email_error[:200])
        return b

    @admin.display(description="")
    def valid(self, obj):
        return badge("Revoked", "#b91c1c") if obj.revoked else ""


class BatchLogInline(admin.TabularInline):
    model = BatchLog
    fields = ["created_at", "user", "action", "comment"]
    readonly_fields = fields
    extra = 0
    max_num = 0
    can_delete = False
    verbose_name_plural = "History"
    classes = ["collapse"]


ACTIONS = {
    # name: (label, statuses, who) — who: organiser / approver
    "generate": "Generate PDFs",
    "submit": "Submit for approval",
    "withdraw": "Withdraw",
    "approve": "Approve",
    "reject": "Reject",
    "test": "Send test email to me",
    "send": "Send to everyone",
    "pause": "Pause sending",
    "retry": "Retry failed",
    "reimport": "Re-read sheet",
}


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    form = BatchForm
    list_display = ["title", "template", "status_badge", "progress", "approver", "created_by", "created_at"]
    list_filter = ["status", "template"]
    search_fields = ["title"]
    inlines = [CertificateInline, BatchLogInline]
    change_form_template = "admin/certs/batch/change_form.html"
    fieldsets = [
        (None, {"fields": ["title", "template", "sheet", "approver"]}),
        ("Text on the certificates", {"fields": ["values_text", "cert_no_format", "cert_no_start"]}),
        ("Email", {"fields": ["from_name", "reply_to", "email_subject", "email_body"], "classes": ["collapse"]}),
    ]

    def get_fieldsets(self, request, obj=None):
        if obj is None or self.has_change_permission(request, obj):
            return self.fieldsets
        return [  # read-only view: show what will actually be used
            (None, {"fields": ["title", "template", "sheet_link", "approver"]}),
            ("Text on the certificates", {"fields": ["values_display", "format_display"]}),
            ("Email", {"fields": ["from_name", "reply_to", "subject_display", "body_display"], "classes": ["collapse"]}),
        ]

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and not self.has_change_permission(request, obj):
            return ["sheet_link", "values_display", "format_display", "subject_display", "body_display"]
        return []

    @admin.display(description="Sheet")
    def sheet_link(self, obj):
        return file_link(obj.sheet, obj.sheet.name.split("/")[-1] if obj.sheet else "")

    @admin.display(description="Values")
    def values_display(self, obj):
        vals = svc.batch_values(obj)
        if not vals:
            return "—"
        return format_html("<table>{}</table>", format_html_join(
            "", "<tr><th style='padding:0 1em 0 0;border:0'>{}</th><td style='border:0;padding:0'>{}</td></tr>", vals.items()))

    @admin.display(description="Certificate no format")
    def format_display(self, obj):
        return svc.cert_format(obj)

    @admin.display(description="Subject")
    def subject_display(self, obj):
        return svc.subject_of(obj)

    @admin.display(description="Body")
    def body_display(self, obj):
        return format_html('<div style="white-space:pre-wrap;max-width:46em">{}</div>', svc.body_of(obj))

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("template", "approver", "created_by").annotate(
            n_total=Count("certificates"),
            n_sent=Count("certificates", filter=Q(certificates__email_status="sent")),
            n_failed=Count("certificates", filter=Q(certificates__email_status="failed")))
        if approver_only(request.user):
            qs = qs.filter(approver=request.user)
        return qs

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.get_status_display(), STATUS_COLOURS.get(obj.status, "#64748b"))

    @admin.display(description="Certificates")
    def progress(self, obj):
        if obj.status in (S.SENDING, S.SENT) or obj.n_sent:
            extra = format_html(' · <span style="color:#b91c1c">{} failed</span>', obj.n_failed) if obj.n_failed else ""
            return format_html("{} · {} sent{}", obj.n_total, obj.n_sent, extra)
        return obj.n_total

    def has_change_permission(self, request, obj=None):
        if obj is not None and not obj.is_editable:
            return False  # locked once submitted; actions still work via the buttons
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status in Batch.ISSUED:
            return False  # issued certificates must stay verifiable
        return super().has_delete_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "approver":
            User = get_user_model()
            kwargs["queryset"] = User.objects.filter(is_active=True).filter(
                Q(is_superuser=True) | Q(groups__permissions__codename="approve_batch")
                | Q(user_permissions__codename="approve_batch")).exclude(pk=request.user.pk).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        triggers = {"sheet", "template", "values_text", "cert_no_format", "cert_no_start"}
        if not change or triggers & set(form.changed_data):
            svc.import_sheet(obj, request.user)
            obj.refresh_from_db()
            if obj.errors:
                messages.error(request, f"The sheet has {len(obj.errors)} problem(s) to fix — see the top of the page.")
            else:
                messages.success(request, f"Read {obj.issues.get('rows', 0)} people from the sheet. "
                                          "Next: Generate PDFs.")

    def response_add(self, request, obj, post_url_continue=None):
        return redirect("admin:certs_batch_change", obj.pk)

    def response_change(self, request, obj):
        if "_save" in request.POST:
            return redirect("admin:certs_batch_change", obj.pk)
        return super().response_change(request, obj)

    def get_urls(self):
        return [path("<int:pk>/do/<str:action>/", self.admin_site.admin_view(self.action_view),
                     name="certs_batch_action")] + super().get_urls()

    def available_actions(self, request, b):
        u = request.user
        org = svc.is_organiser(u) and (u.is_superuser or super().has_change_permission(request, b))
        acts = []
        if org:
            if b.status in (S.DRAFT, S.REJECTED):
                acts += ["reimport", "generate"]
            if b.status == S.READY:
                acts += ["test", "submit", "generate"]
            if b.status == S.PENDING:
                acts += ["withdraw"]
            if b.status == S.APPROVED:
                acts += ["test", "send"]
            if b.status == S.SENDING:
                acts += ["pause"]
            if b.status == S.SENT and b.certificates.filter(email_status="failed").exists():
                acts += ["retry"]
        if svc.can_decide(u, b):
            acts = ["approve", "reject"] + acts
        return acts

    def action_view(self, request, pk, action):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        b = get_object_or_404(self.get_queryset(request), pk=pk)
        if action not in self.available_actions(request, b):
            messages.error(request, "That action isn't available for this batch right now.")
            return redirect("admin:certs_batch_change", pk)
        comment = request.POST.get("comment", "").strip()
        try:
            if action == "generate":
                svc.request_generate(b, request.user)
                messages.success(request, "Generating PDFs in the background. This page refreshes itself.")
            elif action == "reimport":
                svc.import_sheet(b, request.user)
                messages.success(request, "Sheet read again.")
            elif action == "submit":
                svc.submit(b, request.user)
                messages.success(request, f"Sent to {b.approver} for approval.")
            elif action == "withdraw":
                svc.withdraw(b, request.user)
                messages.success(request, "Withdrawn. You can change it and submit again.")
            elif action == "approve":
                svc.approve(b, request.user, comment)
                messages.success(request, "Approved. The organiser has been told they can send.")
            elif action == "reject":
                svc.reject(b, request.user, comment)
                messages.success(request, "Rejected and sent back to the organiser.")
            elif action == "test":
                to = svc.send_test(b, request.user)
                messages.success(request, f"Test email sent to {to}.")
            elif action == "send":
                svc.start_sending(b, request.user)
                messages.success(request, "Sending in the background. This page refreshes itself.")
            elif action == "pause":
                svc.pause_sending(b, request.user)
                messages.success(request, "Paused. Press Send to everyone to carry on.")
            elif action == "retry":
                svc.retry_failed(b, request.user)
                messages.success(request, "Retrying failed emails.")
        except svc.WorkflowError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Something went wrong: {e}")
        return redirect("admin:certs_batch_change", pk)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        b = self.get_object(request, object_id)
        extra = extra_context or {}
        if b is not None:
            certs = b.certificates
            counts = {s: certs.filter(email_status=s).count() for s in ("pending", "sent", "failed")}
            first = certs.exclude(pdf="").first()
            extra.update({
                "batch": b,
                "status_badge": self.status_badge(b),
                "actions": [(a, ACTIONS[a]) for a in self.available_actions(request, b)],
                "n_certs": certs.count(), "counts": counts, "n_revoked": certs.filter(revoked=True).count(),
                "first_pdf": reverse("protected_file", args=[first.pdf.name]) if first else "",
                "effective_values": svc.batch_values(b),
                "subject": svc.subject_of(b), "cert_format": svc.cert_format(b),
                "auto_refresh": b.status in (S.GENERATING, S.SENDING),
                "is_decider": svc.can_decide(request.user, b),
            })
        return super().change_view(request, object_id, form_url, extra)


# ---------------------------------------------------------------- certificates

class CertificateForm(forms.ModelForm):
    class Meta:
        model = Certificate
        fields = ["email", "revoked", "revoked_reason"]


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    form = CertificateForm
    list_display = ["cert_no", "name", "email", "batch", "email_badge", "revoked_badge", "sent_at"]
    list_filter = ["email_status", "revoked", "batch"]
    search_fields = ["cert_no", "name", "email", "code"]
    list_select_related = ["batch"]
    actions = ["resend_email"]
    readonly_fields = ["batch", "row", "name", "cert_no", "code", "pdf_link", "verify_link", "data_table",
                       "email_status", "email_error", "sent_at", "revoked_at"]
    fieldsets = [
        (None, {"fields": ["name", "cert_no", "batch", "row", "pdf_link", "verify_link", "code"]}),
        ("Email", {"fields": ["email", "email_status", "email_error", "sent_at"]}),
        ("Revoke", {"fields": ["revoked", "revoked_reason", "revoked_at"],
                    "description": "A revoked certificate shows as REVOKED on the public verify page."}),
        ("Sheet data", {"fields": ["data_table"], "classes": ["collapse"]}),
    ]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if approver_only(request.user):
            qs = qs.filter(batch__approver=request.user)
        return qs

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and (obj is None or not obj.is_issued)

    @admin.display(description="Email", ordering="email_status")
    def email_badge(self, obj):
        return badge(obj.get_email_status_display(), EMAIL_COLOURS.get(obj.email_status, "#64748b"))

    @admin.display(description="Valid", ordering="revoked")
    def revoked_badge(self, obj):
        return badge("Revoked", "#b91c1c") if obj.revoked else badge("Valid", "#15803d") if obj.is_issued else "—"

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        return file_link(obj.pdf, "View PDF")

    @admin.display(description="Verify page")
    def verify_link(self, obj):
        return format_html('<a href="{}" target="_blank">{}</a>', obj.verify_url, obj.verify_url)

    @admin.display(description="Columns")
    def data_table(self, obj):
        rows = format_html_join("", "<tr><th style='padding-right:1em'>{}</th><td>{}</td></tr>", (obj.data or {}).items())
        return format_html("<table>{}</table>", rows)

    def save_model(self, request, obj, form, change):
        old = Certificate.objects.get(pk=obj.pk)
        if "email" in form.changed_data:
            obj.batch.log(request.user, "Email address changed", f"{obj.cert_no}: {old.email} → {obj.email}", obj)
            if obj.email_status == Certificate.EmailStatus.FAILED:
                obj.email_status = Certificate.EmailStatus.NOT_SENT
        if {"revoked", "revoked_reason"} & set(form.changed_data):
            obj.save(update_fields=["email", "email_status"])
            svc.set_revoked(obj, request.user, obj.revoked, obj.revoked_reason)
            return
        super().save_model(request, obj, form, change)

    @admin.action(description="Resend email to selected people")
    def resend_email(self, request, queryset):
        if not svc.is_organiser(request.user):
            raise PermissionDenied
        ok = 0
        for cert in queryset.select_related("batch", "batch__template")[:50]:
            try:
                svc.resend(cert, request.user)
                ok += 1
            except Exception as e:
                cert.email_status, cert.email_error = Certificate.EmailStatus.FAILED, str(e)[:2000]
                cert.save(update_fields=["email_status", "email_error"])
                messages.error(request, f"{cert.name}: {e}")
        if ok:
            messages.success(request, f"Resent {ok} email(s).")


@admin.register(BatchLog)
class BatchLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "batch", "action", "user", "comment"]
    list_filter = ["action", "user"]
    search_fields = ["batch__title", "comment"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
