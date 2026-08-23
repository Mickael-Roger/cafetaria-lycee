"""Cafetaria service: scrapes the school cafetaria website and stores state locally.

Ported from the original Tom cafetaria MCP module (tom/mcp/cafetaria_server.py),
with the website URL, credentials and refresh frequency driven by config.yml.
"""

import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("cafetaria.service")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"


class CafetariaService:
    """Manages cafetaria reservations and credit for one parent account."""

    def __init__(self, config: Dict[str, Any]):
        cafetaria_config = config.get("cafetaria", {})

        self.username = cafetaria_config["username"]
        self.password = cafetaria_config["password"]

        parsed = urlparse(cafetaria_config["url"])
        self.login_url = cafetaria_config["url"]
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"

        self.db = config["database"]
        self.low_credit_threshold = float(
            cafetaria_config.get("low_credit_threshold", 10.0)
        )
        self.fetch_frequency_minutes = int(config.get("fetch_frequency_minutes", 60))

        # Start stale so the first API access triggers a fetch
        # (same behaviour as the original Tom cafetaria module).
        self.last_update: Optional[datetime] = datetime.now() - timedelta(hours=48)
        self.updating = False
        self.background_status: Dict[str, Any] = {
            "ts": int(time.time()),
            "status": None,
        }

        self._db_lock = threading.Lock()
        # RLock because _change_reservation calls update() while holding it.
        self._scrape_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._init_database()

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _init_database(self):
        try:
            with self._db_lock:
                dbconn = sqlite3.connect(self.db)
                cursor = dbconn.cursor()
                cursor.execute(
                    """
                    create table if not exists cafetaria (
                        date DATETIME PRIMARY KEY,
                        id TEXT,
                        is_reserved BOOLEAN
                    )
                    """
                )
                cursor.execute(
                    """
                    create table if not exists solde (solde TEXT)
                    """
                )
                dbconn.commit()
                dbconn.close()
            logger.info("Database initialized at %s", self.db)
        except Exception:
            logger.exception("Error initializing database")
            raise

    def _find_date(self, date_str: str) -> List[tuple]:
        with self._db_lock:
            dbconn = sqlite3.connect(self.db)
            cursor = dbconn.cursor()
            cursor.execute(
                "SELECT id, is_reserved FROM cafetaria WHERE date = ?", (date_str,)
            )
            entries = cursor.fetchall()
            dbconn.close()
        return entries

    def _store_reservations(self, resas: List[Dict[str, Any]]):
        with self._db_lock:
            dbconn = sqlite3.connect(self.db)
            for resa in resas:
                dbconn.execute(
                    "INSERT OR REPLACE INTO cafetaria (date, id, is_reserved) VALUES (?, ?, ?)",
                    (resa["day"], resa["id"].rstrip(), resa["is_reserved"]),
                )
            dbconn.commit()
            dbconn.close()

    def _store_credit(self, solde: str):
        with self._db_lock:
            dbconn = sqlite3.connect(self.db)
            dbconn.execute("DELETE FROM solde")
            dbconn.execute("INSERT INTO solde (solde) VALUES (?)", (solde,))
            dbconn.commit()
            dbconn.close()

    # ------------------------------------------------------------------
    # Background refresh thread
    # ------------------------------------------------------------------

    def start_background_updates(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_updates, name="cafetaria-update", daemon=True
        )
        self._thread.start()
        logger.info(
            "Background updates started (every %s minutes)",
            self.fetch_frequency_minutes,
        )

    def stop_background_updates(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run_updates(self):
        # Initial fetch shortly after startup so the app has fresh data.
        self._stop_event.wait(2)
        self._safe_update(initial=True)
        while not self._stop_event.wait(self.fetch_frequency_minutes * 60):
            self._safe_update()

    def _safe_update(self, initial: bool = False):
        try:
            self.update()
            return True
        except Exception as exc:
            logger.error("%s update failed: %s", "Initial" if initial else "Auto", exc)
            return False

    def _refresh_if_stale(self, max_age: timedelta):
        """Trigger a fetch when data is stale; fall back to cache on failure."""
        if datetime.now() > (self.last_update + max_age):
            self._safe_update()

    # ------------------------------------------------------------------
    # Website scraping
    # ------------------------------------------------------------------

    def _login(self, session: requests.Session) -> requests.Response:
        data = {
            "txtLogin": self.username,
            "txtMdp": self.password,
            "y": "19",
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": self.login_url,
            "Origin": self.base_url,
        }
        session.get(self.login_url)
        return session.post(self.login_url, data=data, headers=headers)

    def update(self):
        """Refresh credit + reservations from the cafetaria website."""
        with self._scrape_lock:
            self.updating = True
            session = requests.Session()
            try:
                resp_main = self._login(session)
                if resp_main.status_code == 200:
                    soup = BeautifulSoup(resp_main.text, "html.parser")
                    solde_element = soup.find("label", {"for": "CLI_ID"})
                    if solde_element:
                        solde = solde_element.get_text()
                        self._store_credit(solde)

                        match = re.search(r"(\d+,\d+)", solde)
                        if match:
                            amount = float(match.group(1).replace(",", "."))
                            status = None
                            if amount < self.low_credit_threshold:
                                status = f"Only {amount} euros left on cafetaria credit"
                            if status != self.background_status["status"]:
                                self.background_status["ts"] = int(time.time())
                                self.background_status["status"] = status
                        else:
                            logger.error("Could not extract cafetaria credit amount")

                resp_res_main = session.get(f"{self.base_url}/aliReservation.php")
                soup2 = BeautifulSoup(resp_res_main.text, "html.parser")

                pattern = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
                tds = soup2.find_all("td", id=pattern)

                resas = []
                for td in tds:
                    day = td.get("id")
                    links = td.find_all("a")
                    if not links:
                        continue
                    parsed_url = urlparse(links[0].get("href"))
                    query_params = parse_qs(parsed_url.query)
                    resa_id = query_params.get("date", [None])[0]
                    path = parsed_url.path

                    if path == "aliReservationCancel.php":
                        reserved = True
                    elif path == "aliReservationDetail.php":
                        reserved = False
                    else:
                        reserved = None

                    if reserved is not None and resa_id:
                        resas.append(
                            {"day": day, "id": resa_id, "is_reserved": reserved}
                        )

                self._store_reservations(resas)
                self.last_update = datetime.now()
                logger.info("Cafetaria updated successfully (%d days)", len(resas))
            finally:
                try:
                    session.get(f"{self.base_url}/aliDeconnexion.php", timeout=15)
                except Exception:
                    pass
                self.updating = False

    def _change_reservation(self, action: str, resa_id: str) -> bool:
        resa_id = quote(resa_id)

        with self._scrape_lock:
            self.updating = True
            session = requests.Session()
            try:
                self._login(session)

                if action == "cancel":
                    cancel_page = session.get(
                        f"{self.base_url}/aliReservationCancel.php?date={resa_id}"
                    )
                    if cancel_page.status_code != 200:
                        return False

                    values = {
                        "ref": "cancel",
                        "btnOK.x": 42,
                        "btnOK.y": 25,
                        "valide_form": 1,
                    }
                    cancel = session.post(
                        f"{self.base_url}/aliReservationCancel.php",
                        data=values,
                    )
                    if cancel.status_code != 200:
                        return False

                    self.update()
                    return True

                if action == "add":
                    add_page = session.get(
                        f"{self.base_url}/aliReservationDetail.php?date={resa_id}"
                    )
                    if add_page.status_code != 200:
                        return False

                    values = {
                        "CONS_QUANTITE": 1,
                        "restaurant": 1,
                        "btnOK.x": 69,
                        "btnOK.y": 19,
                        "valide_form": 1,
                    }
                    add = session.post(
                        f"{self.base_url}/aliReservationDetail.php",
                        data=values,
                    )
                    if add.status_code != 200:
                        return False

                    self.update()
                    return True

                return False
            except Exception as exc:
                logger.error("Failed to change reservation: %s", exc)
                return False
            finally:
                try:
                    session.get(f"{self.base_url}/aliDeconnexion.php", timeout=15)
                except Exception:
                    pass
                self.updating = False

    # ------------------------------------------------------------------
    # Public operations (same semantics as the MCP tools)
    # ------------------------------------------------------------------

    def add_reservation(self, date_str: str) -> Dict[str, str]:
        """Make a cafetaria reservation for the given day."""
        self._refresh_if_stale(timedelta(0))

        resa = self._find_date(date_str)
        if not resa:
            return {
                "status": "failure",
                "message": "Date not available for reservation",
            }

        resa_id, is_reserved = resa[0]
        if is_reserved:
            return {"status": "success", "message": "Reservation was already done"}

        if self._change_reservation("add", resa_id):
            return {"status": "success", "message": "Reservation done"}
        return {"status": "failure", "message": "Could not make the reservation"}

    def cancel_reservation(self, date_str: str) -> Dict[str, str]:
        """Cancel the cafetaria reservation for the given day."""
        self._refresh_if_stale(timedelta(0))

        resa = self._find_date(date_str)
        if not resa:
            return {"status": "failure", "message": "Date not found"}

        resa_id, is_reserved = resa[0]
        if not is_reserved:
            return {"status": "success", "message": "Reservation was already canceled"}

        if self._change_reservation("cancel", resa_id):
            return {"status": "success", "message": "Reservation canceled"}
        return {"status": "failure", "message": "Could not cancel the reservation"}

    def list_reservations(self) -> List[Dict[str, Any]]:
        """List upcoming cafetaria reservations."""
        self._refresh_if_stale(timedelta(hours=12))

        today = date.today().strftime("%Y-%m-%d")
        with self._db_lock:
            dbconn = sqlite3.connect(self.db)
            cursor = dbconn.cursor()
            cursor.execute(
                "SELECT date, id, is_reserved FROM cafetaria WHERE date >= ? ORDER BY date",
                (today,),
            )
            entries = cursor.fetchall()
            dbconn.close()

        return [
            {"date": entry[0], "id": entry[1], "reserved": bool(entry[2])}
            for entry in entries
        ]

    def get_credit(self) -> Optional[str]:
        """Get the cafetaria credit balance."""
        self._refresh_if_stale(timedelta(days=1))

        with self._db_lock:
            dbconn = sqlite3.connect(self.db)
            res = dbconn.execute("SELECT solde FROM solde").fetchone()
            dbconn.close()

        return res[0] if res else None

    def get_status(self) -> Dict[str, Any]:
        """Return service status for the dashboard."""
        return {
            "last_update": self.last_update.isoformat(timespec="seconds")
            if self.last_update
            else None,
            "updating": self.updating,
            "low_credit_warning": self.background_status.get("status"),
            "fetch_frequency_minutes": self.fetch_frequency_minutes,
        }
