#!/usr/bin/env python3
"""Report how late a scheduled GitHub Actions run actually fired, via Discord.

The workflow is scheduled with cron. GitHub does not guarantee that a scheduled
run starts on time -- runs are queued on a shared pool and can be delayed by
minutes (sometimes a lot more). This script measures that delay and posts it to
a Discord webhook so the drift can be tracked over time.

Three timestamps are compared:

  scheduled : the hour slot this run belongs to (e.g. 14:00:00 UTC)
  queued    : when GitHub actually created the workflow run
  executed  : when this script runs (queued + runner startup + setup steps)

Delays are scheduled -> queued (GitHub's own scheduling lag) and
scheduled -> executed (the total lag before real work happens).

GitHub also drops scheduled triggers outright when it is busy, and never
replays them, so the workflow fires several times an hour as retries. With
--once-per-slot the run stays silent if the CSV log already holds a row for the
current hour, which keeps the report at one message per hour.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("Asia/Bangkok")
except Exception:  # tzdata missing -- fall back to a fixed UTC+7 offset
    LOCAL_TZ = timezone(timedelta(hours=7), name="ICT")

USER_AGENT = "test_gitaction-discord-time-ping/1.0"
CSV_FIELDS = [
    "scheduled_utc",
    "queued_utc",
    "executed_utc",
    "queue_delay_sec",
    "total_delay_sec",
    "run_id",
    "run_number",
    "run_attempt",
    "event",
]


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #

def scheduled_slot(reference: datetime, cron_minute: int) -> datetime:
    """The most recent cron slot at or before ``reference``."""
    slot = reference.replace(minute=cron_minute, second=0, microsecond=0)
    if slot > reference:
        slot -= timedelta(hours=1)
    return slot


def fmt_delay(delta: timedelta) -> str:
    total = int(round(delta.total_seconds()))
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{sign}{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{sign}{minutes}m {seconds}s"
    return f"{sign}{seconds}s"


def fmt_time(moment: datetime) -> str:
    """Both UTC and Bangkok time, since the repo owner reads local time."""
    return (
        f"`{moment.strftime('%Y-%m-%d %H:%M:%S')}` UTC\n"
        f"`{moment.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')}` ICT (UTC+7)"
    )


# --------------------------------------------------------------------------- #
# GitHub context
# --------------------------------------------------------------------------- #

def fetch_run_created_at() -> datetime | None:
    """When GitHub created this workflow run, from the Actions API.

    Returns None outside Actions, or if the API call fails for any reason -- the
    ping is still useful without it, so a failure here must never be fatal.
    """
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    token = os.environ.get("GITHUB_TOKEN")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    if not (repo and run_id and token):
        return None

    request = urllib.request.Request(
        f"{api}/repos/{repo}/actions/runs/{run_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except Exception as exc:  # network, auth, rate limit -- all non-fatal
        print(f"[warn] could not read run metadata: {exc}", file=sys.stderr)
        return None

    created = payload.get("created_at")
    if not created:
        return None
    return datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)


def run_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def build_report(target_minute: int, label: str) -> dict:
    executed = datetime.now(timezone.utc).replace(microsecond=0)
    queued = fetch_run_created_at()

    # Anchor the slot on the queue time when we know it: a very late run could
    # otherwise be attributed to the wrong hour.
    scheduled = scheduled_slot(queued or executed, target_minute)

    return {
        "scheduled": scheduled,
        "queued": queued,
        "executed": executed,
        "queue_delay": (queued - scheduled) if queued else None,
        "total_delay": executed - scheduled,
        "cron": label,
    }


def delay_color(delay: timedelta) -> int:
    seconds = delay.total_seconds()
    if seconds < 120:
        return 0x2ECC71  # green -- basically on time
    if seconds < 600:
        return 0xF1C40F  # yellow -- typical Actions lag
    return 0xE74C3C  # red -- badly late


def build_embed(report: dict) -> dict:
    event = os.environ.get("GITHUB_EVENT_NAME", "local")
    manual = event != "schedule"

    fields = [
        {
            "name": "⏰ เวลาที่ตั้งไว้ (cron slot)",
            "value": fmt_time(report["scheduled"]),
            "inline": True,
        },
        {
            "name": "🏃 เวลาที่รันจริง",
            "value": fmt_time(report["executed"]),
            "inline": True,
        },
    ]

    if report["queued"]:
        fields.append(
            {
                "name": "📥 GitHub สร้าง run เมื่อ",
                "value": fmt_time(report["queued"]),
                "inline": False,
            }
        )

    delay_lines = []
    if report["queue_delay"] is not None:
        delay_lines.append(f"คิวของ GitHub: **{fmt_delay(report['queue_delay'])}**")
    delay_lines.append(f"รวมถึงตอนส่งข้อความ: **{fmt_delay(report['total_delay'])}**")
    fields.append({"name": "⏱️ ดีเลย์", "value": "\n".join(delay_lines), "inline": False})

    meta = [f"cron: `{report['cron']}`", f"event: `{event}`"]
    if report["queued"]:
        meta.append(f"ยิงติดรอบนาทีที่ `:{report['queued'].minute:02d}`")
    if os.environ.get("GITHUB_RUN_NUMBER"):
        meta.append(
            f"run #{os.environ['GITHUB_RUN_NUMBER']}"
            f" (attempt {os.environ.get('GITHUB_RUN_ATTEMPT', '1')})"
        )
    fields.append({"name": "ℹ️ รายละเอียด", "value": " · ".join(meta), "inline": False})

    embed = {
        "title": "🕐 รายงานเวลารัน GitHub Actions" + (" (รันเอง)" if manual else ""),
        "description": (
            "เทียบเวลาที่ตั้ง cron ไว้กับเวลาที่ workflow ได้รันจริง"
            if not manual
            else "รันแบบ manual — ค่าดีเลย์เทียบกับ cron slot ล่าสุดเท่านั้น"
        ),
        "color": delay_color(report["total_delay"]),
        "fields": fields,
        "timestamp": report["executed"].isoformat().replace("+00:00", "Z"),
        "footer": {"text": os.environ.get("GITHUB_REPOSITORY", "local run")},
    }

    link = run_url()
    if link:
        embed["url"] = link
    return embed


def send_to_discord(webhook: str, embed: dict) -> None:
    body = json.dumps({"username": "Actions Time Checker", "embeds": [embed]}).encode()
    request = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print(f"[ok] Discord responded {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise SystemExit(f"[error] Discord rejected the message ({exc.code}): {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"[error] could not reach Discord: {exc.reason}")


def slot_already_logged(path: str, slot: datetime) -> bool:
    """True if a ping for this hour slot was already recorded.

    The CSV is committed back to the repo, so it doubles as the memory that lets
    the extra cron attempts stay quiet once the hour has been covered. A read
    failure answers False: a duplicate ping beats a silently missed hour.
    """
    if not path or not os.path.exists(path):
        return False
    target = slot.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            return any(
                row.get("scheduled_utc") == target for row in csv.DictReader(handle)
            )
    except Exception as exc:
        print(f"[warn] could not read {path}: {exc}", file=sys.stderr)
        return False


def append_csv(path: str, report: dict) -> None:
    exists = os.path.exists(path)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "scheduled_utc": report["scheduled"].strftime("%Y-%m-%d %H:%M:%S"),
                "queued_utc": report["queued"].strftime("%Y-%m-%d %H:%M:%S")
                if report["queued"]
                else "",
                "executed_utc": report["executed"].strftime("%Y-%m-%d %H:%M:%S"),
                "queue_delay_sec": int(report["queue_delay"].total_seconds())
                if report["queue_delay"] is not None
                else "",
                "total_delay_sec": int(report["total_delay"].total_seconds()),
                "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                "run_number": os.environ.get("GITHUB_RUN_NUMBER", ""),
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
            }
        )


def write_summary(report: dict) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    queue_delay = (
        fmt_delay(report["queue_delay"]) if report["queue_delay"] is not None else "n/a"
    )
    queued = (
        report["queued"].strftime("%Y-%m-%d %H:%M:%S UTC") if report["queued"] else "n/a"
    )
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write(
            "## 🕐 Scheduled run delay\n\n"
            "| | UTC | ICT (UTC+7) |\n|---|---|---|\n"
            f"| Scheduled | {report['scheduled'].strftime('%H:%M:%S')} "
            f"| {report['scheduled'].astimezone(LOCAL_TZ).strftime('%H:%M:%S')} |\n"
            f"| Queued | {queued.replace(' UTC', '')} | |\n"
            f"| Executed | {report['executed'].strftime('%H:%M:%S')} "
            f"| {report['executed'].astimezone(LOCAL_TZ).strftime('%H:%M:%S')} |\n\n"
            f"- Queue delay: **{queue_delay}**\n"
            f"- Total delay: **{fmt_delay(report['total_delay'])}**\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-minute",
        type=int,
        default=int(os.environ.get("TARGET_MINUTE", "0") or 0),
        help="the minute past the hour the ping is meant to land on (default: 0)",
    )
    parser.add_argument(
        "--schedule-label",
        default=os.environ.get("SCHEDULE_CRON", "0 * * * *"),
        help="cron expression shown in the report (display only)",
    )
    parser.add_argument(
        "--once-per-slot",
        action="store_true",
        help="stay quiet if this hour was already pinged (schedule events only)",
    )
    parser.add_argument(
        "--csv",
        default=os.environ.get("DELAY_LOG", ""),
        help="append one row per run to this CSV file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload instead of posting it to Discord",
    )
    args = parser.parse_args()

    report = build_report(args.target_minute % 60, args.schedule_label)

    # The workflow fires several times an hour so a dropped trigger does not cost
    # the whole hour. Only the first attempt that gets through reports anything.
    if (
        args.once_per_slot
        and os.environ.get("GITHUB_EVENT_NAME") == "schedule"
        and slot_already_logged(args.csv, report["scheduled"])
    ):
        print(
            f"slot {report['scheduled']:%Y-%m-%d %H:%M} UTC is already covered -- "
            "this is a retry attempt, nothing to send."
        )
        return 0

    embed = build_embed(report)

    print(f"scheduled : {report['scheduled']}")
    print(f"queued    : {report['queued'] or 'unknown'}")
    print(f"executed  : {report['executed']}")
    if report["queue_delay"] is not None:
        print(f"queue delay: {fmt_delay(report['queue_delay'])}")
    print(f"total delay: {fmt_delay(report['total_delay'])}")

    if args.csv:
        append_csv(args.csv, report)
    write_summary(report)

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if args.dry_run or not webhook:
        if not webhook and not args.dry_run:
            print(
                "[error] DISCORD_WEBHOOK_URL is not set -- add it as a repository "
                "secret (Settings > Secrets and variables > Actions).",
                file=sys.stderr,
            )
            print(json.dumps(embed, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(embed, ensure_ascii=False, indent=2))
        return 0

    send_to_discord(webhook, embed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
