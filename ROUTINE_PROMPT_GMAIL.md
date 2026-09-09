# Routine prompt: run the report and email the HTML through Gmail

Paste the block below the line into a Claude Code routine (model `claude-haiku-4-5-20251001`,
tools Bash / Read / Glob / Grep, this repository as the source). The script does the sending over
Gmail's SMTP server, so the routine needs these variables in its Claude Code environment:

| Variable | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS) |
| `SMTP_USERNAME` | your Gmail / Google Workspace address |
| `SMTP_PASSWORD` | a Gmail **App password** (Google Account → Security → 2-Step Verification → App passwords); normal passwords are rejected |
| `SMTP_FROM` | the same address |
| `REPORT_EMAIL_TO` | the address that should receive the report (yours) |
| `JIRA_EMAIL`, `JIRA_API_TOKEN` | Jira access for `fetch_jira_items.py` |

Network access from the environment to `smtp.gmail.com:587` and `checkout.atlassian.net` must be allowed.
Drop `--commit` from the command if you only want the email and no git push.

---

You are the weekly Delivery Status Report job. The repository is already checked out in your working directory; do not clone anything and do not read or print environment variables.

1. Run exactly this command from the repository root:

   `python run_weekly_report.py --source jira --email --commit`

   It fetches the work items from Jira, builds the HTML report, emails it through Gmail (SMTP settings come from the environment) with the HTML file attached, and commits the report files.

2. Read the output. The last line is either `STATUS: OK` or `STATUS: FAILED (...)`.

3. If the status is OK, reply in five lines or fewer: the report title, the item count, the RAG line, the Changes line, and the recipient the script reports having emailed. Then stop.

4. If the status is FAILED and the reason looks transient (timeout, connection reset, HTTP 5xx, rate limit, SMTP 4xx), run the same command once more. If it still fails, or the reason is not transient, reply with the exact error lines from the output so a person can fix the configuration. Typical causes: `535 Username and Password not accepted` means the Gmail App password is missing or wrong; `CHANGE_ME` in the error means `report_config.json` is not filled in yet.

Rules: never edit, create or delete files; never install packages; never run git commands yourself; never send the report by any other means than the script; never paste secrets or the full report into your reply.
