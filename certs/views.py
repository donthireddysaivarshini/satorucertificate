import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.utils._os import safe_join
from django.views.decorators.cache import never_cache

from .models import Batch, Certificate


def site_context(request):
    return {"ORG_NAME": settings.ORG_NAME, "SITE_URL": settings.SITE_URL}


@staff_member_required
def protected_file(request, path):
    """Uploaded templates, sheets and PDFs: only for logged-in staff."""
    try:
        full = Path(safe_join(settings.MEDIA_ROOT, path))
    except Exception:
        raise Http404
    if not full.is_file():
        raise Http404
    if path.startswith("sheets/") and not request.user.has_perm("certs.view_batch"):
        raise Http404
    ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
    inline = ctype.startswith("image/") or ctype == "application/pdf"
    return FileResponse(open(full, "rb"), content_type=ctype, as_attachment=not inline, filename=full.name)


def _issued(qs):
    return qs.filter(batch__status__in=Batch.ISSUED).select_related("batch")


def _same_name(a, b):
    squash = lambda s: " ".join(s.lower().split())
    return squash(a) == squash(b)


@never_cache
def verify(request):
    """Public lookup: by verification code, or by certificate number + name."""
    q = (request.GET.get("q") or "").strip()
    name = (request.GET.get("name") or "").strip()
    ctx = {"q": q, "name": name, "searched": bool(q)}
    if q:
        cert = _issued(Certificate.objects.filter(code__iexact=q.replace(" ", ""))).first()
        if cert:
            return redirect("verify_code", cert.code)
        by_no = _issued(Certificate.objects.filter(cert_no__iexact=q)).first()
        if by_no and not name:
            ctx["need_name"] = True
        elif by_no and _same_name(by_no.name, name):
            return redirect("verify_code", by_no.code)
        else:
            ctx["not_found"] = True
    return render(request, "certs/verify.html", ctx)


@never_cache
def verify_code(request, code):
    cert = _issued(Certificate.objects.filter(code=code.upper())).first()
    if not cert:
        return render(request, "certs/verify.html", {"q": code, "searched": True, "not_found": True}, status=404)
    b = cert.batch
    values = {**(b.template.default_values or {}), **(b.values or {})}
    details = [(k.replace("_", " ").capitalize(), v) for k, v in values.items() if v]
    return render(request, "certs/verify.html", {"cert": cert, "batch": b, "details": details,
                                                 "issued_on": b.decided_at})
