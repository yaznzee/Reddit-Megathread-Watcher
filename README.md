# Reddit Megathread Watcher

Watch new comments in any Reddit megathread (or regular submission) from your terminal.

This project was built as a workaround for situations where Reddit's UI does not make it easy to reliably follow the newest megathread comments.

## Features

- Watch any thread by URL or submission ID
- Poll for new comments and print them live
- Baseline existing comments so you only see new activity after startup
- Optional clipboard copy on detection
- Optional webhook POST for each new comment
- Simple `.env` configuration

## Requirements

- Python 3.10+
- A Reddit API app (client ID + client secret + user agent)

## Installation

```powershell
git clone <your-repo-url>
cd MegathreadWatcher
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Reddit API Setup

1. Go to `https://www.reddit.com/prefs/apps`.
2. Create an app of type `script`.
3. Copy:
- client ID
- client secret
- a custom user agent string (example: `megathread-watcher/1.0 by u/your_username`)

## Configuration

Create your local env file:

```powershell
Copy-Item .env.example .env
```

Fill in:

- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`

Target thread can be provided by:

- CLI `--url`
- CLI `--id`
- `.env` `SUBMISSION_URL`
- `.env` `SUBMISSION_ID`

Priority is: CLI args first, then `.env`.

## Usage

Run with a full Reddit URL:

```powershell
python megawatcher.py --url "https://www.reddit.com/r/subreddit/comments/abc123/thread_title/"
```

Run with submission ID:

```powershell
python megawatcher.py --id abc123
```

Run using only `.env` target values:

```powershell
python megawatcher.py
```

## Environment Variables

Required:

- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT`

Target (choose one):

- `SUBMISSION_URL`
- `SUBMISSION_ID`

Optional:

- `POLL_INTERVAL_SECONDS` (default: `3`)
- `PRINT_WELCOME_COMMENTS` (default: `3`)
- `BASELINE_ALL_EXISTING` (default: `1`)
- `COPY_ON_DETECT` (default: `0`)
- `ALERT_ALL_COMMENTS` (default: `1`)
- `WEBHOOK_URL` (default: empty/off)

## Notes

- Keep `.env` private. It is ignored by `.gitignore`.
- For very large threads, lower polling frequency to reduce API load.
