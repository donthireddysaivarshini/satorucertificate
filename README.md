# certdesk

Certificates with an approval step and public verification, managed from the Django admin.

1. An **organiser** uploads a participant sheet, picks a template, and generates the PDFs.
2. They submit the batch to **one approver**, who reviews it and approves or rejects it with a comment.
3. Only after approval can the organiser **email** the certificates. Sending runs in the background.
4. Each certificate has a **QR code** that opens a public **verify** page showing whether it is *Valid* or *Revoked*.

## Running it on your server (Docker)

You need a VPS with Docker installed and a domain or subdomain pointing at it, such as `certs.yourdomain.com`.

```bash
cp .env.example .env              # fill in DOMAIN, SITE_URL, secrets and SMTP details
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Open `https://certs.yourdomain.com/admin/`. HTTPS is set up automatically by Caddy.

It runs four containers:

- **web:** Django.
- **worker:** generates PDFs and sends emails.
- **db:** PostgreSQL.
- **caddy:** HTTPS.

### Importing your STW workshop template

```bash
docker compose cp template.png web:/tmp/template.png
docker compose cp DMSans.ttf web:/tmp/DMSans.ttf
docker compose exec web python manage.py import_template examples/stw/event.toml \
    --image /tmp/template.png --font /tmp/DMSans.ttf --name "STW Workshop"
```

Then open **Templates → STW Workshop → Layout editor** and use **+ QR code** to place the QR code.

## People and roles

Create users in **Users** and add each one to a group:

- **Organisers:** create templates and batches, generate PDFs, submit, send, revoke certificates, fix email addresses and resend.
- **Approvers:** see only the batches assigned to them, and approve or reject them.

Each user needs **Staff status** ticked to log in, and an email address to receive notifications. Nobody can approve their own batch, not even a superuser.

## A batch, step by step

1. **Batches → Add.** Enter a title, template, sheet (.xlsx or .csv), approver, and any values such as `workshop_date = 8 September 2026`. The sheet is checked straight away. Problems such as bad emails, missing names, or certificate numbers that are duplicated or already used show at the top of the page.
2. **Generate PDFs.** Open a few to check them, or use **Send test email to me**.
3. **Submit for approval.** The approver is emailed a link. After this, the batch can't be edited.
4. The approver chooses **Approve**, or **Reject** with a comment. A rejected batch is editable again: fix it, generate the PDFs again, and resubmit.
5. **Send to everyone.** Progress updates on the page. You can pause sending, and use **Retry failed** if any emails fail.

Every action is recorded with who did it and when. You can see it in each batch's **History** section and in **History** in the sidebar.

## Sheets

- A header row is required.
- **Name** and **Email** columns are recognised under common spellings, such as "Full Name" or "Email ID".
- **Certificate No** is optional:
  - A plain number becomes the full number using the format, so `7` becomes `SF/STW/007/2026`.
  - A full number is used as it is.
  - If there's no number column, set **Number from** to number the rows automatically.
- Every other column can be used as a placeholder. "College Name" becomes `{college_name}`, and a placeholder like `{college}` also finds that column.

## Templates and the layout editor

Upload a blank design under **Templates → Add** (PNG or JPG, about 2000 px wide). Pick its font, which should first be uploaded under **Fonts** (for example DMSans.ttf). Saving takes you to the **layout editor**.

In the editor, each field is a **box**: drag it into place, and pull its corners to resize it. Text is vertically centred in its box, aligned left, centre or right, and shrinks to fit if it's too wide. The editor measures text with the template's own font, and **Preview certificate** shows the real rendering, so what you see is what prints.

Where a box's text comes from:

- **A column in the spreadsheet:** the column heading, such as `Name` or `College`. Matching ignores case and spaces, and `College` also finds a "College Name" column.
- **Same text on every certificate:** fixed text.
- **A pattern:** text with `{placeholders}`, such as `Certificate No : {cert_no}` or `{workshop_date}`. Placeholders can be column headings (`{College Name}`) or batch values (`{workshop_date}`, `{hours}`, `{issued}`).

Use **{cert_no}** for the certificate number. It is the formatted number (`SF/STW/007/2026`) that is checked for duplicates and shown on the verify page. Building the number yourself from the sheet column, as in `SF/STW/{Certificate No}/2026`, would print a raw value such as `7`, which wouldn't match the verify page.

The preset **Workshop Date**, **Total Hours** and **Issued On** fields use batch values. That way one template works for every workshop: you set the dates on each batch.

Other options:

- **QR code** draws a square code linking to the certificate's verify page.
- **Fill the box with a colour first** paints over anything printed on the design under the box, such as a placeholder line.

Approvers can open the editor but can't change it. If a layout is changed after a batch's PDFs were made, that batch has to be generated again before it can be submitted.

## Verification

- `https://your-domain/v/<code>/` is the address the QR code opens.
- `https://your-domain/verify/` lets anyone search by verification code, or by certificate number **plus** the name on the certificate. Requiring the name stops people from guessing numbers to collect names.
- Only approved batches are verifiable.
- Revoking a certificate (**Certificates → open one → Revoked**) makes its page show **Revoked**, along with the reason.

## Running it on your own computer

```bash
pip install -r requirements.txt
export DJANGO_DEBUG=1 EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
python manage.py migrate && python manage.py init_roles && python manage.py createsuperuser
python manage.py runserver          # terminal 1
python manage.py worker             # terminal 2
```

This uses SQLite, and emails are printed in terminal 1 instead of being sent.

## Backups

```bash
docker compose exec db pg_dump -U certdesk certdesk > backup-$(date +%F).sql
docker run --rm -v certdesk_media:/m -v "$PWD":/b alpine tar czf /b/media-$(date +%F).tgz -C /m .
```

The database holds the records and the media volume holds the templates, sheets and PDFs. Back up both.

## Updating

```bash
git pull && docker compose up -d --build
```

Database changes are applied automatically when the web container starts.

## Tests

```bash
DJANGO_DEBUG=1 python manage.py test certs
```
