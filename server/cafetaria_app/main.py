"""Cafetaria PWA server: REST API + static PWA hosting."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import AuthManager
from .config import load_config
from .service import CafetariaService

logger = logging.getLogger("cafetaria.api")

COOKIE_NAME = "cafetaria_token"

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


def create_app(config_path: Optional[str] = None) -> FastAPI:
    config = load_config(config_path)

    logging.basicConfig(
        level=getattr(
            logging,
            str(config["server"].get("log_level", "INFO")).upper(),
            logging.INFO,
        ),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Configuration loaded from %s", config["_config_path"])

    service = CafetariaService(config)
    auth = AuthManager(config["users"], config["server"]["secret_key"])

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.start_background_updates()
        logger.info("Cafetaria server ready (domain: %s)", config["server"]["domain"])
        yield
        service.stop_background_updates()

    app = FastAPI(
        title="Cafetaria Server",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _get_token(request: Request) -> Optional[str]:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:].strip()
        return request.cookies.get(COOKIE_NAME)

    def get_current_user(request: Request) -> str:
        token = _get_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        username = auth.verify_token(token)
        if not username:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        return username

    def validate_date(date_str: str) -> str:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Date must use the YYYY-MM-DD format"
            )
        return date_str

    # ------------------------------------------------------------------
    # Auth endpoints
    # ------------------------------------------------------------------

    @app.post("/api/login")
    def login(payload: Dict[str, Any], response: Response):
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if not auth.verify_credentials(username, password):
            raise HTTPException(status_code=401, detail="Invalid username or password")

        token = auth.issue_token(username)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            max_age=30 * 24 * 3600,
            path="/",
        )
        return {"token": token, "username": username}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(user: str = Depends(get_current_user)):
        return {"username": user}

    # ------------------------------------------------------------------
    # Cafetaria endpoints (feature-equivalent to the MCP tools)
    # ------------------------------------------------------------------

    @app.get("/api/reservations")
    def reservations(user: str = Depends(get_current_user)):
        return JSONResponse(service.list_reservations())

    @app.post("/api/reservations/{date_str}")
    def make_reservation(date_str: str, user: str = Depends(get_current_user)):
        validate_date(date_str)
        result = service.add_reservation(date_str)
        status_code = 200 if result["status"] == "success" else 409
        return JSONResponse(result, status_code=status_code)

    @app.delete("/api/reservations/{date_str}")
    def cancel_reservation(date_str: str, user: str = Depends(get_current_user)):
        validate_date(date_str)
        result = service.cancel_reservation(date_str)
        status_code = 200 if result["status"] == "success" else 409
        return JSONResponse(result, status_code=status_code)

    @app.get("/api/credit")
    def credit(user: str = Depends(get_current_user)):
        value = service.get_credit()
        return {"credit": value}

    @app.get("/api/status")
    def status(user: str = Depends(get_current_user)):
        return service.get_status()

    @app.post("/api/sync")
    def sync(user: str = Depends(get_current_user)):
        if service.updating:
            return {"ok": True, "message": "Update already in progress"}
        try:
            service.update()
        except Exception as exc:
            logger.error("Manual sync failed: %s", exc)
            return JSONResponse(
                {"ok": False, "message": "Update failed"}, status_code=502
            )
        return {"ok": True}

    # ------------------------------------------------------------------
    # Static PWA files
    # ------------------------------------------------------------------

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:
        logger.warning("Web directory not found at %s - API only", WEB_DIR)

    return app


app = create_app(os.environ.get("CAFETARIA_CONFIG"))


def main():
    config = load_config(os.environ.get("CAFETARIA_CONFIG"))
    host = config["server"].get("host", "127.0.0.1")
    port = int(config["server"].get("port", 8080))

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
