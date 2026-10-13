#!/usr/bin/env python
"""
One-click startup for Mbarara Auction System.

Double-click start.bat (Windows) or run ./start.sh (Linux/macOS). With no input
from you it will:

    1. create a virtual environment (.venv)            [first run only]
    2. install dependencies                            [first run / when requirements change]
    3. create a .env file with a fresh SECRET_KEY      [first run only]
    4. run database migrations
    5. load demo data (users, auctions, bids)          [first run only]
    6. start the auction scheduler (activates scheduled auctions, closes ended ones)
    7. start the web server and open your browser

Options:
    python start.py --reset        wipe the database and start fresh (re-seeds)
    python start.py --no-seed      don't load demo data
    python start.py --no-browser   don't open the browser
    python start.py --no-server    set everything up, then exit
    python start.py --port 8080    use a different port
"""
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# Windows consoles default to a legacy code page that can't print the symbols below.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.platform == "win32":
    os.system("")  # enables ANSI colour codes in cmd.exe / PowerShell

SEED_USER = "seed_admin"
SEED_PASSWORD = "Seed@12345"
SCHEDULER_INTERVAL_SECONDS = 30
MIN_PYTHON = (3, 10)  # Django 5.2 / crispy-bootstrap5 2026.x need 3.10+

VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


class C:
    GREEN, BLUE, YELLOW, RED, RESET = "\033[92m", "\033[94m", "\033[93m", "\033[91m", "\033[0m"


def step(msg): print(f"{C.BLUE}→ {msg}{C.RESET}", flush=True)
def ok(msg): print(f"{C.GREEN}✓ {msg}{C.RESET}", flush=True)
def warn(msg): print(f"{C.YELLOW}⚠ {msg}{C.RESET}", flush=True)


def fail(msg):
    print(f"{C.RED}✗ {msg}{C.RESET}", flush=True)
    sys.exit(1)


def run(cmd, check=True, quiet=False):
    """Run a command with the venv's interpreter environment. Returns True on success."""
    kwargs = {"cwd": ROOT, "env": os.environ.copy()}
    if quiet:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run([str(c) for c in cmd], **kwargs)
    if check and result.returncode != 0:
        fail(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return result.returncode == 0


def manage(*args, check=True, quiet=False):
    return run([VENV_PYTHON, "manage.py", *args], check=check, quiet=quiet)


# --------------------------------------------------------------------------- setup
def create_venv():
    if VENV_PYTHON.exists():
        ok("Virtual environment ready")
        return
    step("Creating virtual environment (first run)...")
    run([sys.executable, "-m", "venv", VENV])
    ok("Virtual environment created")


def install_dependencies():
    req = ROOT / ("requirements-dev.txt" if (ROOT / "requirements-dev.txt").exists() else "requirements.txt")
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    stamp = VENV / ".requirements.sha256"
    if stamp.exists() and stamp.read_text().strip() == digest:
        ok("Dependencies up to date")
        return
    step(f"Installing dependencies from {req.name} (this can take a minute)...")
    run([VENV_PYTHON, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=False)
    run([VENV_PYTHON, "-m", "pip", "install", "-r", req])
    stamp.write_text(digest)
    ok("Dependencies installed")


def create_env_file():
    env_file = ROOT / ".env"
    if env_file.exists():
        ok(".env file found")
        return
    step("Creating .env configuration...")
    env_file.write_text(
        "DJANGO_SETTINGS_MODULE=config.settings.development\n"
        f"SECRET_KEY={secrets.token_urlsafe(50)}\n"
        "DEBUG=True\n"
        "ALLOWED_HOSTS=localhost,127.0.0.1\n"
        "\n# SQLite - no PostgreSQL needed locally\n"
        "DATABASE_URL=sqlite:///db.sqlite3\n"
        "CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000\n"
        "\n# Emails print to this window\n"
        "EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend\n"
        "\n# Run background tasks in-process - no Redis needed locally\n"
        "CELERY_TASK_ALWAYS_EAGER=True\n"
        "CELERY_TASK_EAGER_PROPAGATES=False\n"
        "REDIS_URL=redis://localhost:6379/0\n"
        "\nSQL_DEBUG_LEVEL=WARNING\n",
        encoding="utf-8",
    )
    ok(".env created")


def needs_seed():
    """True when the demo admin doesn't exist yet (fresh database)."""
    code = (
        "import sys; from django.contrib.auth import get_user_model as g; "
        f"sys.exit(0 if g().objects.filter(username='{SEED_USER}').exists() else 1)"
    )
    return not manage("shell", "-c", code, check=False, quiet=True)


def seed():
    step("Loading demo data...")
    manage("seed_data", "--password", SEED_PASSWORD)


def reset_database():
    step("Resetting database...")
    for name in ("db.sqlite3", "db.sqlite3-journal"):
        f = ROOT / name
        if f.exists():
            f.unlink()
    ok("Database deleted")


# --------------------------------------------------------------------------- runtime
def pick_port(preferred):
    for port in range(preferred, preferred + 11):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        warn(f"Port {port} is busy, trying the next one...")
    fail(f"No free port found between {preferred} and {preferred + 10}")


def scheduler_loop(stop):
    """Stand-in for Celery beat: activate due auctions and close ended ones."""
    while not stop.wait(SCHEDULER_INTERVAL_SECONDS):
        for command in ("activate_scheduled_auctions", "close_ended_auctions"):
            manage(command, check=False, quiet=True)


def open_browser_when_ready(port):
    for _ in range(60):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(f"http://localhost:{port}")
                return
        time.sleep(0.5)


def print_banner(port, seeded):
    print(f"\n{C.GREEN}{'=' * 60}")
    print("Mbarara Auction System is running!")
    print("=" * 60)
    print(f"Site:   {C.BLUE}http://localhost:{port}{C.GREEN}")
    print(f"Admin:  {C.BLUE}http://localhost:{port}/admin{C.GREEN}")
    if seeded:
        print(f"Login:  {SEED_USER} / {SEED_PASSWORD}   (admin)")
        print(f"        officer_moses, seller_agnes, bidder_john ... same password")
    print("Press CTRL+C to stop")
    print(f"{'=' * 60}{C.RESET}\n", flush=True)


def run_server(port, open_browser, seeded):
    port = pick_port(port)
    print_banner(port, seeded)
    stop = threading.Event()
    threading.Thread(target=scheduler_loop, args=(stop,), daemon=True).start()
    if open_browser:
        threading.Thread(target=open_browser_when_ready, args=(port,), daemon=True).start()
    try:
        manage("runserver", f"127.0.0.1:{port}", check=False)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        print(f"\n{C.YELLOW}Server stopped.{C.RESET}")


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return
    if sys.version_info < MIN_PYTHON:
        fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; you have {sys.version.split()[0]}. "
             "Install it from https://www.python.org/downloads/")

    port = 8000
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            fail("--port needs a number, e.g. --port 8080")

    print(f"{C.BLUE}{'=' * 60}\nMbarara Auction System - Auto Setup\n{'=' * 60}{C.RESET}")

    try:
        create_venv()
        install_dependencies()
        create_env_file()
        if "--reset" in args:
            reset_database()
        step("Applying database migrations...")
        manage("migrate", "--verbosity", "0")
        ok("Database ready")

        seeded = False
        if "--no-seed" not in args and needs_seed():
            seed()
            seeded = True
        elif "--no-seed" not in args:
            ok("Demo data already loaded")
            seeded = True

        if "--no-server" in args:
            ok("Setup complete (--no-server given, not starting the server)")
            return
        run_server(port, open_browser="--no-browser" not in args, seeded=seeded)
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Cancelled.{C.RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
