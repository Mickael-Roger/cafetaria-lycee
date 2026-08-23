# Cafeteria — PWA reservation manager

A Progressive Web App (PWA) to manage school cafeteria meal reservations from an
Android phone (also works from any desktop browser). Feature-equivalent to the
original `tom/mcp/cafetaria_server.py` MCP module:

| Original MCP tool              | Application feature                                    |
| ------------------------------ | ------------------------------------------------------ |
| `get_cafetaria_credit`         | Credit balance shown on the dashboard (`GET /api/credit`) |
| `list_cafetaria_reservations`  | Upcoming days list with reserved state (`GET /api/reservations`) |
| `make_a_cafetaria_reservation` | "Reserve" button / `POST /api/reservations/{date}`     |
| `cancel_a_cafetaria_reservation` | "Cancel" button / `DELETE /api/reservations/{date}`  |
| low-credit notification        | Warning banner + `GET /api/status`                     |
| background auto-update thread  | Background refresh every `fetch_frequency_minutes`     |

The application has two parts:

1. **Server** (`server/`) — a Python FastAPI application that:
   - logs into the cafeteria website with the configured credentials,
   - scrapes reservations and credit into a local SQLite database,
   - exposes an authenticated JSON REST API,
   - serves the PWA static files,
   - keeps data fresh in the background.

2. **Web PWA** (`web/`) — a mobile-first installable app:
   - login screen, credit card, next-reserved-meal highlight,
   - one-tap Reserve / Cancel per day,
   - offline support (service worker caches the shell + last known data),
   - installable on Android home screen ("Add to home screen" prompt).

---

## Project layout

```
cafetaria-lycee/
├── Dockerfile              # container image (config/data mounted at /data)
├── .dockerignore
├── config.yml.example      # configuration template — copy to config.yml
├── config.yml              # your actual configuration (never commit)
├── server/
│   ├── requirements.txt
│   ├── run.py              # entrypoint: python run.py [--config path]
│   ├── cafetaria_app/
│   │   ├── config.py       # YAML loading & validation
│   │   ├── service.py      # cafeteria website scraping + SQLite storage
│   │   ├── auth.py         # app users + signed session tokens
│   │   └── main.py         # FastAPI app (REST API + static hosting)
│   └── tests/
│       ├── mock_site.py    # mock of the cafeteria website
│       └── e2e_test.py     # end-to-end test suite
└── web/                    # PWA frontend served by the server
    ├── index.html
    ├── manifest.webmanifest
    ├── sw.js               # service worker (offline support)
    ├── css/app.css
    ├── js/app.js
    └── icons/
```

## Configuration

Everything is configured in **`config.yml`** (see `config.yml.example`):

```yaml
server:
  host: 0.0.0.0            # bind address
  port: 8080
  domain: cafetaria.example.com   # public domain of the app
  secret_key: <random secret>     # signs auth tokens — CHANGE IT
  log_level: INFO

cafetaria:
  url: https://webparent.paiementdp.com/aliAuthentification.php?site=aes00152
  username: <cafeteria website login>
  password: <cafeteria website password>
  low_credit_threshold: 10.0      # € below which the warning banner shows

fetch_frequency_minutes: 60       # background refresh interval

database: ./data/cafetaria.sqlite

users:                            # application users (PWA logins)
  - username: parent
    password: parent123
```

The config file is looked up at (in order): `--config` argument →
`CAFETARIA_CONFIG` environment variable → `<repo>/config.yml`.

> Generate a strong secret key, e.g.:
> `python3 -c "import secrets; print(secrets.token_hex(32))"`

## Running

```bash
cd cafetaria/server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../config.yml.example ../config.yml     # then edit it
.venv/bin/python run.py
```

The server listens on `server.host:server.port`. Open `http://<domain>:<port>/`
in a browser, sign in with an application user, and use the app.

### Running with Docker

Build the image:

```bash
docker build -t cafetaria-lycee .
```

Prepare a host directory for configuration and data, put your edited
`config.yml` in it, then run:

```bash
mkdir -p /opt/cafetaria && cp config.yml.example /opt/cafetaria/config.yml
# edit /opt/cafetaria/config.yml:
#   - set server.secret_key, cafetaria credentials and users
#   - set database: /data/cafetaria.sqlite
docker run -d --name cafetaria \
  -p 8080:8080 \
  -v /opt/cafetaria:/data \
  --restart unless-stopped \
  cafetaria-lycee
```

Notes:

- The container expects the configuration at `/data/config.yml`
  (`CAFETARIA_CONFIG` is preset) — no secrets are baked into the image.
- `/data` is declared a volume: it holds `config.yml` and the SQLite database,
  so reservations survive container upgrades.
- Set `database: /data/cafetaria.sqlite` in the mounted config so the DB lands
  in the persistent volume.
- Make sure the `server.port` in the config matches the published port.

Or with docker compose:

```yaml
services:
  cafetaria:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./config.yml:/data/config.yml:ro
      - cafetaria-data:/data
    restart: unless-stopped

volumes:
  cafetaria-data:
```

### Production notes

- Put the server behind a reverse proxy (nginx/caddy) providing HTTPS on the
  configured `server.domain` — HTTPS is required for PWA installation and the
  service worker.
- The SQLite database lives at the `database:` path; back it up if needed.

## Installing on Android

1. Open the app URL in Chrome.
2. Sign in, then tap the **Install** banner (or Chrome menu → *Add to Home
   screen*).
3. The app launches full-screen from the home screen like a native app.

## Tests

An end-to-end suite spins up a mock of the cafeteria website plus the real
server and exercises login, listing, reserving, cancelling, credit and status:

```bash
cd cafetaria/server
../.venv-cafetaria/bin/python tests/e2e_test.py    # or any python with requests
```

## API summary

All `/api/*` endpoints (except `/api/login`) require
`Authorization: Bearer <token>` (or the session cookie set by `/api/login`).

| Method | Path                        | Description                          |
| ------ | --------------------------- | ------------------------------------ |
| POST   | `/api/login`                | `{username, password}` → `{token}`   |
| POST   | `/api/logout`               | Clear session cookie                 |
| GET    | `/api/me`                   | Current user                         |
| GET    | `/api/reservations`         | Upcoming days `[ {date, id, reserved} ]` |
| POST   | `/api/reservations/{date}`  | Make a reservation (`YYYY-MM-DD`)    |
| DELETE | `/api/reservations/{date}`  | Cancel the reservation               |
| GET    | `/api/credit`               | `{credit: "..."}`                    |
| GET    | `/api/status`               | `{last_update, updating, low_credit_warning, fetch_frequency_minutes}` |
| POST   | `/api/sync`                 | Force an immediate refresh           |

Dates always use the `%Y-%m-%d` format, as in the original module.
