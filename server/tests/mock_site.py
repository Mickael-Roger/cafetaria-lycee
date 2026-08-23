#!/usr/bin/env python3
"""Mock of the school cafetaria website (paiementdp.com) used for testing.

Implements the same pages/flow the real site exposes:
  - GET/POST /aliAuthentification.php?site=...   (login)
  - GET      /aliReservation.php                 (reservation grid)
  - GET/POST /aliReservationDetail.php?date=...  (make a reservation)
  - GET/POST /aliReservationCancel.php?date=...  (cancel a reservation)
  - GET      /aliDeconnexion.php                 (logout)

Run:  python mock_site.py [port]
"""

import sys
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VALID_LOGIN = "mock-user"
VALID_PASSWORD = "mock-pass"
# sid -> {"selected": last date opened via detail/cancel page} (like the real site)
SESSIONS = {}
LOCK = threading.Lock()

CREDIT = "Solde : 42,50 €"


def default_days():
    """20 upcoming days; some pre-reserved."""
    days = {}
    base = date.today()
    for offset in range(1, 21):
        d = (base + timedelta(days=offset)).strftime("%Y-%m-%d")
        days[d] = offset % 3 == 0  # every third day reserved
    return days


DAYS = default_days()


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logs
        pass

    def _session(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if part.strip().startswith("sid="):
                return part.strip()[4:]
        return None

    def _html(self, body, status=200, headers=None):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _form(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode() if length else ""
        return parse_qs(raw)

    # ------------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/aliAuthentification.php":
            self._html(
                "<html><body><form><input name='txtLogin'/><input name='txtMdp'/></form></body></html>"
            )

        elif parsed.path == "/aliReservation.php":
            if not self._session():
                return self._redirect("/aliAuthentification.php?site=mock")
            rows = []
            with LOCK:
                snapshot = dict(DAYS)
            for day, reserved in sorted(snapshot.items()):
                page = "aliReservationCancel" if reserved else "aliReservationDetail"
                label = "Cancel meal" if reserved else "Reserve meal"
                rows.append(
                    f'<tr><td id="{day}"><a href="{page}.php?date={day}">{label}</a></td></tr>'
                )
            self._html(
                "<html><body><table>" + "".join(rows) + "</table>"
                f"<label for='CLI_ID'>{CREDIT}</label></body></html>"
            )

        elif parsed.path in ("/aliReservationCancel.php", "/aliReservationDetail.php"):
            if not self._session():
                return self._redirect("/aliAuthentification.php?site=mock")
            qs = parse_qs(parsed.query)
            day = (qs.get("date") or [None])[0]
            if not day or day not in DAYS:
                return self._html("<html><body>Unknown date</body></html>", status=404)
            with LOCK:
                SESSIONS.setdefault(self._session(), {})["selected"] = day
            verb = "cancel" if "Cancel" in parsed.path else "reserve"
            self._html(
                f"<html><body><form method='post'>"
                f"<input type='hidden' name='valide_form' value='1'/>"
                f"<button>Confirm {verb} {day}</button></form></body></html>"
            )

        elif parsed.path == "/aliDeconnexion.php":
            with LOCK:
                SESSIONS.pop(self._session(), None)
            self._html("<html><body>Bye</body></html>")

        else:
            self._html("<html><body>Not found</body></html>", status=404)

    # ------------------------------------------------------------------
    def do_POST(self):
        parsed = urlparse(self.path)
        form = self._form()

        if parsed.path == "/aliAuthentification.php":
            if (
                form.get("txtLogin", [""])[0] == VALID_LOGIN
                and form.get("txtMdp", [""])[0] == VALID_PASSWORD
            ):
                sid = f"s{len(SESSIONS) + 1}-{id(self)}"
                with LOCK:
                    SESSIONS[sid] = {}
                return self._html(
                    "<html><body><h1>Welcome</h1>"
                    f"<label for='CLI_ID'>{CREDIT}</label></body></html>",
                    headers={"Set-Cookie": f"sid={sid}; Path=/"},
                )
            return self._html("<html><body>Bad login</body></html>", status=200)

        elif parsed.path in ("/aliReservationCancel.php", "/aliReservationDetail.php"):
            sid = self._session()
            if not sid or sid not in SESSIONS:
                return self._redirect("/aliAuthentification.php?site=mock")
            qs = parse_qs(parsed.query)
            day = (qs.get("date") or [None])[0]
            if not day:
                # Like the real site: fall back to the date selected in session.
                day = SESSIONS[sid].get("selected")
            if not day:
                return self._html("<html><body>Missing date</body></html>", status=400)
            with LOCK:
                DAYS[day] = "Cancel" not in parsed.path
                SESSIONS[sid]["selected"] = None
            return self._html(f"<html><body>OK - {day} updated</body></html>")

        else:
            self._html("<html><body>Not found</body></html>", status=404)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9911
    server = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    print(f"Mock cafetaria site on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
