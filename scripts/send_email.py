#!/usr/bin/env python3
"""Send daily pipeline digest email with attachments.

Usage:
    python send_email.py report.md [--attachments file1.tex file2.pdf ...] [--dry-run]

Reads SMTP config from config/automation.json. The SMTP_PASSWORD environment
variable overrides the config file value (for secret management).
"""

import argparse
import json
import os
import smtplib
import ssl
import sys
import zipfile
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import date


def load_config(config_path: Path) -> dict:
    """Load email config from automation.json, with env var overrides."""
    with open(config_path) as f:
        config = json.load(f)

    email_cfg = config.get("email", {})

    # Environment variables override config file values
    email_cfg["smtp_password"] = os.environ.get(
        "SMTP_PASSWORD", email_cfg.get("smtp_password", "")
    )
    if os.environ.get("SMTP_USER"):
        email_cfg["smtp_user"] = os.environ["SMTP_USER"]
    if os.environ.get("SMTP_FROM"):
        email_cfg["from_email"] = os.environ["SMTP_FROM"]
    if os.environ.get("SMTP_TO"):
        email_cfg["to_email"] = os.environ["SMTP_TO"]

    return email_cfg


def markdown_to_html(md_text: str) -> str:
    """Convert a simple markdown report to HTML for email body."""
    lines = md_text.split("\n")
    html_lines = []
    in_table = False

    for line in lines:
        stripped = line.strip()

        # Headers
        if stripped.startswith("# "):
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("## "):
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("### "):
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
        # Table separator line
        elif stripped.startswith("|---") or stripped.startswith("| ---"):
            continue
        # Table row
        elif stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                html_lines.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; margin: 10px 0;">')
                in_table = True
                html_lines.append("<tr>" + "".join(f"<th style='background:#f0f0f0; text-align:left;'>{c}</th>" for c in cells) + "</tr>")
            else:
                html_lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        # List items
        elif stripped.startswith("- "):
            html_lines.append(f"<li>{stripped[2:]}</li>")
        # Bold lines
        elif stripped.startswith("**") and stripped.endswith("**"):
            html_lines.append(f"<p><strong>{stripped[2:-2]}</strong></p>")
        # Empty line
        elif not stripped:
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append("<br>")
        # Regular text
        else:
            # Handle inline bold
            text = stripped
            while "**" in text:
                text = text.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
            html_lines.append(f"<p>{text}</p>")

    if in_table:
        html_lines.append("</table>")

    return "<html><body>" + "\n".join(html_lines) + "</body></html>"


def attach_files(msg: MIMEMultipart, file_paths: list[Path], archive_name: str):
    """Create a ZIP archive of files and attach it to the email."""
    if not file_paths:
        return

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in file_paths:
                if fp.exists():
                    # Preserve relative path structure in the ZIP
                    # e.g., cv/aliz_ai_engineer/main.pdf -> cv/aliz_ai_engineer/main.pdf
                    arcname = str(fp.relative_to(fp.parent.parent.parent)) if fp.parent.parent.parent.exists() else fp.name
                    zf.write(fp, arcname)

        with open(tmp_path, "rb") as f:
            part = MIMEBase("application", "zip")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{archive_name}"')
        msg.attach(part)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def send_email(config: dict, report_path: Path, attachments: list[Path]):
    """Send the email via SMTP."""
    msg = MIMEMultipart()
    msg["From"] = f"{config.get('from_name', 'Job Search Pipeline')} <{config['from_email']}>"
    msg["To"] = config["to_email"]
    msg["Subject"] = f"Job Search Daily Report - {date.today().isoformat()}"

    # Read and convert report to HTML
    with open(report_path) as f:
        md_text = f.read()
    html = markdown_to_html(md_text)
    msg.attach(MIMEText(html, "html"))

    # Also attach the raw markdown
    with open(report_path) as f:
        raw_part = MIMEText(f.read(), "plain")
        msg.attach(raw_part)

    # Attach ZIP of application packages
    if attachments:
        attach_files(msg, attachments, f"applications_{date.today().isoformat()}.zip")

    # Send via SMTP
    # Try standard SSL first, fall back to unverified context for macOS cert issues
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
            server.starttls(context=context)
            server.login(config["smtp_user"], config["smtp_password"])
            server.send_message(msg)
    except ssl.SSLCertVerificationError:
        # macOS sometimes lacks the cert bundle; retry without verification
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
            server.starttls(context=context)
            server.login(config["smtp_user"], config["smtp_password"])
            server.send_message(msg)


def main():
    parser = argparse.ArgumentParser(description="Send daily pipeline digest email")
    parser.add_argument("report", type=Path, help="Daily report markdown file")
    parser.add_argument("--attachments", nargs="*", type=Path, default=[], help="Files to attach")
    parser.add_argument("--config", type=Path, default=Path("config/automation.json"), help="Config file path")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be sent without sending")
    args = parser.parse_args()

    if not args.report.exists():
        print(f"Error: report file {args.report} not found", file=sys.stderr)
        sys.exit(1)

    if not args.config.exists():
        print(f"Error: config file {args.config} not found", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)

    if not config.get("enabled"):
        print("Email disabled in config, skipping.")
        return

    if not config.get("smtp_user") or not config.get("to_email"):
        print("Error: smtp_user and to_email must be set in config or env vars", file=sys.stderr)
        sys.exit(1)

    existing_attachments = [a for a in args.attachments if a.exists()]

    if args.dry_run:
        print(f"DRY RUN: would send report {args.report}")
        print(f"  To: {config['to_email']}")
        print(f"  Subject: Job Search Daily Report - {date.today().isoformat()}")
        print(f"  Attachments: {len(existing_attachments)} files")
        for a in existing_attachments:
            print(f"    - {a}")
        return

    try:
        send_email(config, args.report, existing_attachments)
        print(f"Email sent to {config['to_email']}")
    except Exception as e:
        print(f"Error sending email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
