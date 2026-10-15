import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.utils import OperationalError, ProgrammingError

from certs import services
from certs.models import Batch

log = logging.getLogger("certs")


class Command(BaseCommand):
    help = "Background worker: generates PDFs and sends emails for batches that are waiting."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="process what's waiting, then exit")
        parser.add_argument("--interval", type=float, default=3.0)

    def handle(self, *args, once=False, interval=3.0, **opts):
        self.stdout.write("certdesk worker started")
        while True:
            busy = False
            try:
                close_old_connections()
                for b in Batch.objects.filter(status=Batch.Status.GENERATING):
                    self.stdout.write(f"Generating: {b}")
                    services.generate(b)
                    busy = True
                for b in Batch.objects.filter(status=Batch.Status.SENDING):
                    self.stdout.write(f"Sending: {b}")
                    services.send_pending(b)
                    busy = True
            except (OperationalError, ProgrammingError) as e:
                # database not ready yet (e.g. during startup or migrations)
                self.stderr.write(f"Database not ready: {e}")
                time.sleep(5)
            except Exception:
                log.exception("Worker error")
                time.sleep(5)
            if once:
                break
            if not busy:
                time.sleep(interval)
