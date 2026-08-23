#!/usr/bin/env python3
"""End-to-end test: mock cafetaria website + application server.

Usage:
    python tests/e2e_test.py
"""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import requests

SERVER_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = SERVER_DIR.parent
PYTHON = sys.executable

MOCK_PORT = 9911
APP_PORT = 9910
BASE = f"http://127.0.0.1:{APP_PORT}"

PASSED = 0


def check(name, condition, extra=""):
    global PASSED
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" - {extra}" if extra and not condition else ""))
    if not condition:
        sys.exit(1)
    PASSED += 1


def wait_ready(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(url, timeout=2)
            return True
        except requests.RequestException:
            time.sleep(0.4)
    return False


def main():
    # ------------------------------------------------------------------
    # Test configuration
    # ------------------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="cafetaria-e2e-")
    config_path = os.path.join(tmp, "config.yml")
    db_path = os.path.join(tmp, "cafetaria.sqlite")
    with open(config_path, "w") as fh:
        fh.write(f"""
server:
  host: 127.0.0.1
  port: {APP_PORT}
  domain: cafetaria.test.local
  secret_key: e2e-test-secret-abcdef0123456789
  log_level: INFO

cafetaria:
  url: http://127.0.0.1:{MOCK_PORT}/aliAuthentification.php?site=mock00152
  username: mock-user
  password: mock-pass
  low_credit_threshold: 50.0

fetch_frequency_minutes: 60

database: {db_path}

users:
  - username: parent
    password: parent123
  - username: teen
    password: teen456
""")

    procs = []
    try:
        # ------------------------------------------------------------------
        # Start mock site + app server
        # ------------------------------------------------------------------
        procs.append(
            subprocess.Popen(
                [PYTHON, str(SERVER_DIR / "tests" / "mock_site.py"), str(MOCK_PORT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        env = dict(os.environ, CAFETARIA_CONFIG=config_path)
        procs.append(
            subprocess.Popen(
                [PYTHON, str(SERVER_DIR / "run.py")],
                cwd=str(SERVER_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        )

        check(
            "Mock site ready",
            wait_ready(f"http://127.0.0.1:{MOCK_PORT}/aliReservation.php"),
        )
        check("App server ready", wait_ready(BASE))

        # ------------------------------------------------------------------
        # Static PWA hosting
        # ------------------------------------------------------------------
        r = requests.get(BASE + "/")
        check("GET / serves PWA", r.status_code == 200 and "Cafeteria" in r.text)
        r = requests.get(BASE + "/manifest.webmanifest")
        check("Manifest served", r.status_code == 200)
        r = requests.get(BASE + "/sw.js")
        check(
            "Service worker served",
            r.status_code == 200 and "cafetaria-cache" in r.text,
        )

        # ------------------------------------------------------------------
        # Authentication
        # ------------------------------------------------------------------
        r = requests.post(
            BASE + "/api/login", json={"username": "parent", "password": "wrong"}
        )
        check("Login rejects bad password", r.status_code == 401)

        r = requests.get(BASE + "/api/reservations")
        check("API requires auth", r.status_code == 401)

        r = requests.post(
            BASE + "/api/login", json={"username": "parent", "password": "parent123"}
        )
        check("Login OK", r.status_code == 200 and "token" in r.json())
        token = r.json()["token"]
        hdrs = {"Authorization": f"Bearer {token}"}

        r = requests.get(BASE + "/api/me", headers=hdrs)
        check("GET /api/me", r.status_code == 200 and r.json()["username"] == "parent")

        r = requests.get(
            BASE + "/api/me", headers={"Authorization": "Bearer forged.token"}
        )
        check("Forged token rejected", r.status_code == 401)

        # ------------------------------------------------------------------
        # Reservations
        # ------------------------------------------------------------------
        r = requests.get(BASE + "/api/reservations", headers=hdrs)
        check("List reservations", r.status_code == 200)
        reservations = r.json()
        check(
            "Reservations fetched from mock site",
            len(reservations) >= 15,
            f"got {len(reservations)}",
        )
        reserved_days = [r_["date"] for r_ in reservations if r_["reserved"]]
        available_days = [r_["date"] for r_ in reservations if not r_["reserved"]]
        check("Pre-reserved days visible", len(reserved_days) > 0)

        target_free = available_days[0]
        target_reserved = reserved_days[0]

        # Bad date format
        r = requests.post(BASE + "/api/reservations/2026-13-99", headers=hdrs)
        check("Invalid date rejected", r.status_code == 400)

        # Reserve a free day
        r = requests.post(BASE + "/api/reservations/" + target_free, headers=hdrs)
        check(
            "Make reservation",
            r.status_code == 200 and r.json()["status"] == "success",
            r.text,
        )

        r = requests.get(BASE + "/api/reservations", headers=hdrs)
        now_reserved = {r_["date"]: r_["reserved"] for r_ in r.json()}
        check("New reservation reflected", now_reserved[target_free] is True)

        # Reserving again -> idempotent success
        r = requests.post(BASE + "/api/reservations/" + target_free, headers=hdrs)
        check(
            "Double reservation is idempotent",
            r.status_code == 200 and "already" in r.json()["message"],
        )

        # Cancel a reserved day
        r = requests.delete(BASE + "/api/reservations/" + target_reserved, headers=hdrs)
        check(
            "Cancel reservation",
            r.status_code == 200 and r.json()["status"] == "success",
            r.text,
        )

        r = requests.get(BASE + "/api/reservations", headers=hdrs)
        now_reserved = {r_["date"]: r_["reserved"] for r_ in r.json()}
        check("Cancellation reflected", now_reserved[target_reserved] is False)

        # Cancel again -> idempotent success
        r = requests.delete(BASE + "/api/reservations/" + target_reserved, headers=hdrs)
        check(
            "Double cancel is idempotent",
            r.status_code == 200 and "already" in r.json()["message"],
        )

        # Unknown date
        unknown = (date.today() + timedelta(days=400)).strftime("%Y-%m-%d")
        r = requests.post(BASE + "/api/reservations/" + unknown, headers=hdrs)
        check("Unknown date fails cleanly", r.status_code == 409)

        # ------------------------------------------------------------------
        # Credit + status
        # ------------------------------------------------------------------
        r = requests.get(BASE + "/api/credit", headers=hdrs)
        check(
            "Credit endpoint",
            r.status_code == 200 and "42,50" in r.json()["credit"],
            r.text,
        )

        r = requests.get(BASE + "/api/status", headers=hdrs)
        data = r.json()
        check("Status has last_update", bool(data["last_update"]))
        check(
            "Low credit warning raised (< threshold)",
            "42.5" in (data["low_credit_warning"] or ""),
            str(data.get("low_credit_warning")),
        )

        # Manual sync
        r = requests.post(BASE + "/api/sync", headers=hdrs)
        check("Manual sync", r.status_code == 200 and r.json()["ok"] is True)

        # ------------------------------------------------------------------
        # Second user
        # ------------------------------------------------------------------
        r = requests.post(
            BASE + "/api/login", json={"username": "teen", "password": "teen456"}
        )
        check("Second user login", r.status_code == 200)
        token2 = r.json()["token"]
        r = requests.get(
            BASE + "/api/me", headers={"Authorization": f"Bearer {token2}"}
        )
        check("Second user identified", r.json()["username"] == "teen")

        # Logout clears cookie
        s = requests.Session()
        r = s.post(
            BASE + "/api/login", json={"username": "teen", "password": "teen456"}
        )
        r = s.get(BASE + "/api/me")
        check("Cookie auth works", r.status_code == 200)
        s.post(BASE + "/api/logout")
        r = s.get(BASE + "/api/me")
        check("Cookie cleared on logout", r.status_code == 401)

        # Database file exists with expected tables
        conn = sqlite3.connect(db_path)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        check("SQLite tables created", {"cafetaria", "solde"} <= tables)

        print(f"\nAll {PASSED} checks passed.")

    finally:
        for proc in procs:
            proc.send_signal(signal.SIGINT)
        for proc in procs:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        if len(procs) > 1 and procs[1].stdout:
            out = procs[1].stdout.read().decode(errors="replace")
            if "Traceback" in out:
                print("--- server output ---")
                print(out[-3000:])


if __name__ == "__main__":
    main()
