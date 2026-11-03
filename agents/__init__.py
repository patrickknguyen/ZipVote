# agents/__init__.py
#
# API keys live in ZipVote/.env, which git ignores (see .env.example). They
# are loaded here, for the agents only, so other programs started from your
# shell (like the Next.js dev server) never see them. A key already set in
# the shell takes precedence over the file.
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:   # python-dotenv not installed: keys must come from the shell
    pass
