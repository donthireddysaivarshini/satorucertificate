from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

ROLES = {
    # Prepare templates and batches, send approved batches, revoke certificates.
    "Organisers": [
        "view_font", "add_font", "change_font", "delete_font",
        "view_template", "add_template", "change_template",
        "view_batch", "add_batch", "change_batch", "delete_batch",
        "view_certificate", "change_certificate",
        "view_batchlog",
    ],
    # Review batches assigned to them and approve or reject.
    "Approvers": [
        "view_template", "view_batch", "approve_batch", "view_certificate", "view_batchlog",
    ],
}


class Command(BaseCommand):
    help = "Create (or update) the Organisers and Approvers groups with the right permissions."

    def handle(self, *args, **opts):
        for name, codenames in ROLES.items():
            group, _ = Group.objects.get_or_create(name=name)
            perms = Permission.objects.filter(content_type__app_label="certs", codename__in=codenames)
            group.permissions.set(perms)
            self.stdout.write(f"{name}: {perms.count()} permissions")
