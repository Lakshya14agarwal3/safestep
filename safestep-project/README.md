# SafeStep — AI Environmental & Disaster Risk Advisor

Built for **Sketch 'n' Ship with AI** (GDG Noida) by Team Quantum Minds.

SafeStep takes a location and a planned activity, pulls live weather, air-quality, and
disaster-alert data, and uses Claude to reason over all three signals together — returning
a plain-language **Proceed / Caution / Avoid** verdict instead of raw numbers on five
different dashboards.

## Architecture

```
Frontend (React, single HTML file)
        │  POST /assess { location, activity, when }
        ▼
Backend (FastAPI)
        │
        ├─ Geocode location            → OpenWeatherMap Geocoding API
        ├─ Fetch weather (parallel)    → OpenWeatherMap
        ├─ Fetch air quality (parallel)→ OpenWeatherMap Air Pollution API
        ├─ Fetch disaster alerts (parallel) → GDACS RSS feed
        │
        ▼
   Claude (claude-sonnet-5)
   reasons over all signals + the stated activity
        │
        ▼
   Strict JSON verdict → returned to frontend
        │
        ▼
   Verdict card + map (Leaflet) rendered to user
```

## Stack

- **Backend:** FastAPI (Python), httpx for async parallel API calls, Anthropic SDK
- **Frontend:** React 18 (CDN, no build step) + Tailwind CSS (CDN) + Leaflet for the map
- **Data sources:** OpenWeatherMap (weather, AQI, geocoding — one free key covers all three), GDACS (disaster alerts, no key needed)
- **Reasoning:** Claude API, single call with structured JSON output

## Setup

### 1. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and fill in:
#   OPENWEATHER_API_KEY   (free: https://home.openweathermap.org/users/sign_up)
#   ANTHROPIC_API_KEY     (https://console.anthropic.com/)

uvicorn main:app --reload --port 8000
```

Check it's running: open `http://localhost:8000/health` — should show both keys as `true`.

> **Note:** a fresh OpenWeatherMap key can take up to ~2 hours to activate. Get the key
> early on hackathon day.

### 2. Frontend

The frontend is a single static file — no npm install needed.

```bash
cd frontend
python3 -m http.server 5500
```

Open `http://localhost:5500`. It talks to the backend at `http://localhost:8000` by default.

To point it at a different backend URL (e.g. once deployed), add this line **above** the
main `<script type="text/babel">` block in `index.html`:

```html
<script>window.SAFESTEP_API_BASE = "https://your-backend-url.com";</script>
```

## Deploying for submission (recommended — judges prefer live links over localhost)

- **Backend:** [Render](https://render.com) or [Railway](https://railway.app) free tier — connect your GitHub repo, set the root to `backend/`, add the env vars from `.env.example` in their dashboard, start command `uvicorn main:app --host 0.0.0.0 --port $PORT`.
- **Frontend:** [Vercel](https://vercel.com) or [Netlify](https://netlify.com) — deploy the `frontend/` folder as a static site. Set `window.SAFESTEP_API_BASE` to your deployed backend URL before shipping.

## Demo script (for judging)

1. Enter a real location prone to weather swings (e.g. a hill station or coastal city) and an activity like "Trekking / hiking."
2. Watch the radar-sweep loading state — narrate that it's live-checking weather, AQI, and disaster feeds in parallel.
3. Show the verdict card, then expand "Show full breakdown" to reveal the reasoning and raw source data — this demonstrates it's not a canned response.
4. Point out the map pin and mention the roadmap: IMD/NDMA integration, voice output in regional languages, SMS alerts for users without smartphones.

## Known limitations (be upfront with judges)

- GDACS covers major global disasters (earthquakes, cyclones, floods, etc.) but isn't as
  granular as India-specific NDMA/IMD advisories — flagged in the pitch as a v2 integration.
- OpenWeatherMap's free tier AQI uses their own 1–5 index rather than India's official AQI
  scale; close enough for a hackathon demo, worth noting if asked.
- No caching layer — repeated identical requests re-hit all APIs. Fine for a demo, add
  Redis caching for production.

## Team

Lakshya · Priyanshu Sharma · Mohit Prajapat · Nikhil Singhal — Team Quantum Minds
