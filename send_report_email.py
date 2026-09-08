#!/usr/bin/env python3
"""
Email the Delivery Status Report over SMTP (standard library only).

Settings come from environment variables so no credentials live in the repo:
    SMTP_HOST          e.g. smtp.gmail.com
    SMTP_PORT          587 (STARTTLS, default) or 465 (implicit TLS)
    SMTP_USERNAME      login user (leave empty for an unauthenticated relay)
    SMTP_PASSWORD      login password / app password
    SMTP_FROM          sender address (defaults to SMTP_USERNAME)
    SMTP_STARTTLS      "false" to disable STARTTLS on ports other than 465
    REPORT_EMAIL_TO    comma-separated recipients (falls back to email.to in report_config.json)
    REPORT_EMAIL_CC    comma-separated CC recipients (falls back to email.cc)

Standalone use:
    python send_report_email.py --html reports/latest.html --markdown reports/latest.md --subject "Delivery Status Report"
    python send_report_email.py --html reports/latest.html --dry-run      # compose only, print headers
"""
import argparse
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

HERE = Path(__file__).resolve().parent


class EmailError(RuntimeError):
    """Configuration or delivery problem with a readable message."""


def split_addresses(value):
    return [part.strip() for part in (value or "").replace(";", ",").split(",") if part.strip()]


def settings_from_env(email_config=None):
    """Collect SMTP settings from the environment, falling back to report_config.json for recipients."""
    email_config = email_config or {}
    username = os.environ.get("SMTP_USERNAME", "").strip()
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "").strip() or 587),
        "username": username,
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "sender": os.environ.get("SMTP_FROM", "").strip() or username,
        "starttls": os.environ.get("SMTP_STARTTLS", "true").strip().lower() not in ("0", "false", "no", "off"),
        "to": split_addresses(os.environ.get("REPORT_EMAIL_TO")) or list(email_config.get("to") or []),
        "cc": split_addresses(os.environ.get("REPORT_EMAIL_CC")) or list(email_config.get("cc") or []),
    }


def missing_settings(settings):
    missing = []
    if not settings["host"]:
        missing.append("SMTP_HOST")
    if not settings["sender"]:
        missing.append("SMTP_FROM (or SMTP_USERNAME)")
    if not settings["to"]:
        missing.append("REPORT_EMAIL_TO (or email.to in report_config.json)")
    return missing


def is_configured(settings):
    return not missing_settings(settings)


def wrap_html_document(title, fragment):
    """Turn the report fragment into a complete HTML document for email clients and browsers."""
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{safe_title}</title>\n</head>\n"
        "<body style=\"margin: 24px; background-color: #FFFFFF;\">\n"
        f"{fragment}\n</body>\n</html>\n"
    )


def build_message(subject, html_body, text_body, settings, attachments=()):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["sender"]
    message["To"] = ", ".join(settings["to"])
    if settings["cc"]:
        message["Cc"] = ", ".join(settings["cc"])
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    for path in attachments:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".html", ".htm"):
            maintype, subtype = "text", "html"
        elif suffix in (".md", ".markdown"):
            maintype, subtype = "text", "markdown"
        elif suffix == ".json":
            maintype, subtype = "application", "json"
        else:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return message


def send_message(message, settings):
    """Deliver a composed message; returns the recipients it was sent to."""
    missing = missing_settings(settings)
    if missing:
        raise EmailError("Email is not configured; set " + ", ".join(missing))
    recipients = settings["to"] + settings["cc"]
    context = ssl.create_default_context()
    try:
        if settings["port"] == 465:
            server = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=60, context=context)
        else:
            server = smtplib.SMTP(settings["host"], settings["port"], timeout=60)
        with server:
            server.ehlo()
            if settings["port"] != 465 and settings["starttls"]:
                server.starttls(context=context)
                server.ehlo()
            if settings["username"]:
                server.login(settings["username"], settings["password"])
            server.send_message(message, from_addr=settings["sender"], to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as err:
        detail = err.smtp_error.decode(errors="replace") if isinstance(err.smtp_error, bytes) else str(err.smtp_error)
        raise EmailError(f"SMTP login failed for {settings['username']}: {detail}") from None
    except (smtplib.SMTPException, OSError) as err:
        raise EmailError(f"SMTP delivery via {settings['host']}:{settings['port']} failed: {err}") from None
    return recipients


def send_report(subject, html_body, text_body, settings, attachments=()):
    """Compose and send the report in one call; returns the recipients."""
    message = build_message(subject, html_body, text_body, settings, attachments)
    return send_message(message, settings)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Email a generated report over SMTP.")
    parser.add_argument("--html", required=True, help="HTML file used as the email body and first attachment")
    parser.add_argument("--markdown", help="Markdown file used as the plain-text alternative and second attachment")
    parser.add_argument("--subject", default="Delivery Status Report")
    parser.add_argument("--config", default=str(HERE / "report_config.json"))
    parser.add_argument("--dry-run", action="store_true", help="compose the message and print its headers without sending")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # non-ASCII safe on Windows consoles

    try:
        from local_env import load_dotenv
        load_dotenv(HERE / ".env")
    except ImportError:
        pass

    email_config = {}
    config_path = Path(args.config)
    if config_path.exists():
        email_config = json.loads(config_path.read_text(encoding="utf-8")).get("email", {})
    settings = settings_from_env(email_config)

    html_body = Path(args.html).read_text(encoding="utf-8")
    text_body = Path(args.markdown).read_text(encoding="utf-8") if args.markdown else "See the attached report."
    attachments = [args.html] + ([args.markdown] if args.markdown else [])

    try:
        message = build_message(args.subject, html_body, text_body, settings, attachments)
        if args.dry_run:
            for header in ("From", "To", "Cc", "Subject"):
                print(f"{header}: {message[header] or ''}")
            print(f"Parts: {[part.get_content_type() for part in message.walk() if not part.is_multipart()]}")
            missing = missing_settings(settings)
            print("Ready to send." if not missing else "Missing settings: " + ", ".join(missing))
            return 0
        recipients = send_message(message, settings)
    except EmailError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"Sent '{args.subject}' to {', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
