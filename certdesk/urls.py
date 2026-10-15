from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from certs import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="verify", permanent=False)),
    path("verify/", views.verify, name="verify"),
    path("v/<str:code>/", views.verify_code, name="verify_code"),
    path("files/<path:path>", views.protected_file, name="protected_file"),
    path("admin/", admin.site.urls),
]
