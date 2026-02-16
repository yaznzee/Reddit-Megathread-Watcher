#!/usr/bin/env python3
"""
sora_watcher.py - watch new comments in any Reddit submission (megathread).

Required environment variables:
  REDDIT_CLIENT_ID
  REDDIT_CLIENT_SECRET
  REDDIT_USER_AGENT

Target submission can be provided in one of three ways (priority order):
  1) CLI: --url https://www.reddit.com/r/.../comments/<id>/.../
  2) CLI: --id <submission_id>
  3) ENV: SUBMISSION_URL or SUBMISSION_ID

Optional environment variables:
  WEBHOOK_URL=https://...
  COPY_ON_DETECT=1
  ALERT_ALL_COMMENTS=1
  PRINT_WELCOME_COMMENTS=3   # show last N existing comments at startup
  POLL_INTERVAL_SECONDS=3
  BASELINE_ALL_EXISTING=1    # mark current comments as seen on startup
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

import praw
from prawcore.exceptions import RequestException, ResponseException, ServerError

# .env support (optional)
try:
    from dotenv import load_dotenv  # pip install python-dotenv

    load_dotenv()
except Exception:
    pass

# Optional clipboard copy
try:
    import pyperclip  # pip install pyperclip
except Exception:
    pyperclip = None

# Optional color output
try:
    from colorama import Fore, Style, init as colorama_init  # pip install colorama

    colorama_init()
    COLOR_OK = True
except Exception:
    COLOR_OK = False

    class _NoColor:
        RESET_ALL = ""

    class _NoFore:
        CYAN = GREEN = YELLOW = RED = MAGENTA = ""

    Style = _NoColor()
    Fore = _NoFore()

SUBMISSION_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.IGNORECASE)


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else str(v).strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name, "")
        return int(raw.strip() or default)
    except Exception:
        return default


def now_local_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def print_banner(submission_id: str, title: str = "", subreddit: str = "", source: str = "") -> None:
    line = "=" * 72
    print(line)
    print(f"{'Reddit Megathread Live Comment Watcher':^72}")
    print(line)
    print(f"Start time: {now_local_str()}")
    print(f"Submission ID: {submission_id}")
    if source:
        print(f"Source: {source}")
    if title:
        print(f"Title: {title}")
    if subreddit:
        print(f"Subreddit: r/{subreddit}")
    print(line)
    sys.stdout.flush()


def fmt_author(comment) -> str:
    try:
        return comment.author.name if comment.author else "[deleted]"
    except Exception:
        return "[unknown]"


def print_comment(comment) -> None:
    created = datetime.fromtimestamp(comment.created_utc, tz=timezone.utc).astimezone()
    author = fmt_author(comment)
    permalink = f"https://www.reddit.com{comment.permalink}"
    header = f"[{created:%Y-%m-%d %H:%M:%S %Z}] u/{author} | id: {comment.id} | parent: {comment.parent_id}"

    if COLOR_OK:
        print(Fore.CYAN + header + Style.RESET_ALL)
        print(Fore.GREEN + "-" * len(header) + Style.RESET_ALL)
        print(comment.body)
        print(Fore.YELLOW + permalink + Style.RESET_ALL)
        print()
    else:
        print(header)
        print("-" * len(header))
        print(comment.body)
        print(permalink)
        print()
    sys.stdout.flush()


def maybe_copy_to_clipboard(text: str, enabled: bool) -> None:
    if enabled and pyperclip is not None:
        try:
            pyperclip.copy(text)
        except Exception:
            pass


def maybe_post_webhook(webhook_url: Optional[str], comment) -> None:
    if not webhook_url:
        return
    try:
        import requests  # pip install requests
    except Exception:
        return

    payload = {
        "type": "reddit_comment",
        "timestamp": now_local_str(),
        "comment": {
            "id": comment.id,
            "author": fmt_author(comment),
            "body": comment.body,
            "permalink": f"https://www.reddit.com{comment.permalink}",
            "created": datetime.fromtimestamp(comment.created_utc, tz=timezone.utc).astimezone().isoformat(),
        },
        "submission_id": getattr(getattr(comment, "submission", None), "id", None),
    }

    try:
        requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    except Exception:
        pass


def get_reddit():
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT")

    missing = [
        key
        for key, value in (
            ("REDDIT_CLIENT_ID", client_id),
            ("REDDIT_CLIENT_SECRET", client_secret),
            ("REDDIT_USER_AGENT", user_agent),
        )
        if not value
    ]

    if missing:
        print("Missing required environment variables:", ", ".join(missing), file=sys.stderr)
        sys.exit(1)

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        ratelimit_seconds=60,
    )

    try:
        reddit.read_only = True
    except Exception:
        pass

    return reddit


def extract_submission_id(submission_url: str) -> Optional[str]:
    text = submission_url.strip()
    if not text:
        return None

    match = SUBMISSION_ID_RE.search(text)
    if match:
        return match.group(1)

    # Allow raw IDs entered in URL field.
    if re.fullmatch(r"[a-z0-9]{5,8}", text, flags=re.IGNORECASE):
        return text

    return None


def resolve_target_submission(args: argparse.Namespace) -> tuple[str, str]:
    # Priority: CLI --url / --id, then env vars.
    source_url = (args.url or "").strip()
    source_id = (args.id or "").strip()
    env_url = os.getenv("SUBMISSION_URL", "").strip()
    env_id = os.getenv("SUBMISSION_ID", "").strip()

    if source_url:
        sid = extract_submission_id(source_url)
        if not sid:
            print("Could not parse submission ID from --url.", file=sys.stderr)
            sys.exit(1)
        return sid, f"--url {source_url}"

    if source_id:
        if not re.fullmatch(r"[a-z0-9]{5,8}", source_id, flags=re.IGNORECASE):
            print("--id must look like a Reddit submission id (base36).", file=sys.stderr)
            sys.exit(1)
        return source_id, f"--id {source_id}"

    if env_url:
        sid = extract_submission_id(env_url)
        if not sid:
            print("Could not parse submission ID from SUBMISSION_URL.", file=sys.stderr)
            sys.exit(1)
        return sid, "SUBMISSION_URL"

    if env_id:
        if not re.fullmatch(r"[a-z0-9]{5,8}", env_id, flags=re.IGNORECASE):
            print("SUBMISSION_ID must look like a Reddit submission id (base36).", file=sys.stderr)
            sys.exit(1)
        return env_id, "SUBMISSION_ID"

    print(
        "Missing target submission. Provide --url, --id, SUBMISSION_URL, or SUBMISSION_ID.",
        file=sys.stderr,
    )
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch new comments in a Reddit submission/megathread."
    )
    parser.add_argument("--url", help="Full Reddit submission URL")
    parser.add_argument("--id", help="Reddit submission id (base36)")
    return parser.parse_args()


def fetch_all_comments(submission):
    submission.comment_sort = "new"
    submission.comment_limit = None
    submission.comments.replace_more(limit=0)
    comments = list(submission.comments.list())
    comments.sort(key=lambda c: getattr(c, "created_utc", 0))
    return comments


def fetch_recent_comments(submission, limit: int = 5):
    try:
        comments = fetch_all_comments(submission)
        if limit <= 0:
            return []
        return comments[-limit:]
    except Exception:
        return []


def poll_new_comments(submission, seen_ids: set[str]):
    try:
        comments = fetch_all_comments(submission)
    except Exception:
        return []

    new_items = []
    for c in comments:
        cid = getattr(c, "id", None)
        if not cid or cid in seen_ids:
            continue
        new_items.append(c)

    for c in new_items:
        seen_ids.add(c.id)

    return new_items


def main() -> None:
    args = parse_args()
    submission_id, source = resolve_target_submission(args)

    copy_on_detect = env_bool("COPY_ON_DETECT", False)
    alert_all = env_bool("ALERT_ALL_COMMENTS", True)
    webhook_url = os.getenv("WEBHOOK_URL", "").strip() or None
    poll_interval = max(1, env_int("POLL_INTERVAL_SECONDS", 3))
    welcome_n = max(0, env_int("PRINT_WELCOME_COMMENTS", 3))
    baseline_all = env_bool("BASELINE_ALL_EXISTING", True)

    reddit = get_reddit()
    submission = reddit.submission(id=submission_id)

    try:
        title = submission.title
        subreddit_name = str(submission.subreddit)
    except Exception:
        title = ""
        subreddit_name = ""

    print_banner(submission_id, title=title, subreddit=subreddit_name, source=source)

    seen_ids: set[str] = set()

    if baseline_all:
        try:
            existing = fetch_all_comments(submission)
            for c in existing:
                if getattr(c, "id", None):
                    seen_ids.add(c.id)
            if COLOR_OK:
                print(
                    Fore.MAGENTA
                    + f"Baselined {len(seen_ids)} existing comments. Watching for new ones..."
                    + Style.RESET_ALL
                )
            else:
                print(f"Baselined {len(seen_ids)} existing comments. Watching for new ones...")
        except Exception:
            pass

    if welcome_n > 0:
        recent = fetch_recent_comments(submission, limit=welcome_n)
        if recent:
            if COLOR_OK:
                print(
                    Fore.MAGENTA
                    + f"Showing last {len(recent)} existing comments (not treated as new):"
                    + Style.RESET_ALL
                )
            else:
                print(f"Showing last {len(recent)} existing comments (not treated as new):")
            for c in recent:
                print_comment(c)
                if getattr(c, "id", None):
                    seen_ids.add(c.id)
        else:
            if COLOR_OK:
                print(Fore.MAGENTA + "No existing comments found yet. Waiting for new ones..." + Style.RESET_ALL)
            else:
                print("No existing comments found yet. Waiting for new ones...")

    backoff = 5
    max_backoff = 120
    consecutive_empty = 0

    while True:
        try:
            new_comments = poll_new_comments(submission, seen_ids)
            if new_comments:
                consecutive_empty = 0
                backoff = 5
                if alert_all:
                    for c in new_comments:
                        print_comment(c)
                        maybe_copy_to_clipboard(c.body, copy_on_detect)
                        maybe_post_webhook(webhook_url, c)
            else:
                consecutive_empty += 1
                if consecutive_empty % 10 == 0:
                    if COLOR_OK:
                        print(
                            Fore.YELLOW
                            + f"[{now_local_str()}] (heartbeat) no new comments yet..."
                            + Style.RESET_ALL
                        )
                    else:
                        print(f"[{now_local_str()}] (heartbeat) no new comments yet...")

            time.sleep(poll_interval)
        except (RequestException, ResponseException, ServerError) as exc:
            if COLOR_OK:
                print(
                    Fore.RED
                    + f"[warn] API/network error: {exc}. Retrying in {backoff}s..."
                    + Style.RESET_ALL,
                    file=sys.stderr,
                )
            else:
                print(f"[warn] API/network error: {exc}. Retrying in {backoff}s...", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(max_backoff, backoff * 2)
            continue
        except KeyboardInterrupt:
            print("\nStopping watcher. Goodbye!")
            break
        except Exception:
            if COLOR_OK:
                print(Fore.MAGENTA + "[error] Unexpected exception (continuing):" + Style.RESET_ALL, file=sys.stderr)
            else:
                print("[error] Unexpected exception (continuing):", file=sys.stderr)
            traceback.print_exc()
            time.sleep(2)


if __name__ == "__main__":
    main()
