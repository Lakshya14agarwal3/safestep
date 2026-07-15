"""
SafeStep backend — assesses environmental & disaster risk for a location + activity.

Flow:
  1. Geocode the free-text location (OpenWeatherMap Geocoding API)
  2. Fetch weather, air quality, and disaster alerts in parallel
  3. Hand the combined data to NVIDIA NIM (z-ai/glm-5.2), which returns a strict-JSON verdict
  4. Return the verdict + raw supporting data to the frontend

Run:
  pip install -r requirements.txt
  cp .env.example .env   # then fill in your API keys
  uvicorn main:app --reload --port 8000
"""

import os
import math
import asyncio
import json
from xml.etree import ElementTree
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
OPENAQ_API_KEY = os.getenv("OPENAQ_API_KEY", "")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

app = FastAPI(title="SafeStep API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

nim_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
) if NVIDIA_API_KEY else None


class AssessRequest(BaseModel):
    location: str
    activity: str
    when: Optional[str] = None  # e.g. "this weekend", "Friday evening" — free text, optional

    @field_validator("location", "activity")
    @classmethod
    def not_empty_or_too_long(cls, v: str, info) -> str:
        v = v.strip()
        if not v:
            raise ValueError(f"{info.field_name} must not be empty")
        if len(v) > 200:
            raise ValueError(f"{info.field_name} must be 200 characters or fewer")
        return v


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

async def geocode(location: str, client: httpx.AsyncClient) -> dict:
    resp = await client.get(
        "https://api.openweathermap.org/geo/1.0/direct",
        params={"q": location, "limit": 1, "appid": OPENWEATHER_API_KEY},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise HTTPException(status_code=404, detail=f"Couldn't find location: {location}")
    return {
        "lat": data[0]["lat"],
        "lon": data[0]["lon"],
        "name": data[0].get("name", location),
        "state": data[0].get("state", ""),
        "country": data[0].get("country", ""),
    }


async def get_weather(lat: float, lon: float, client: httpx.AsyncClient) -> dict:
    try:
        current = await client.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lon, "appid": OPENWEATHER_API_KEY, "units": "metric"},
        )
        forecast = await client.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"lat": lat, "lon": lon, "appid": OPENWEATHER_API_KEY, "units": "metric"},
        )
        current.raise_for_status()
        forecast.raise_for_status()
        c = current.json()
        f = forecast.json()
        next_slots = [
            {
                "time": slot["dt_txt"],
                "temp": slot["main"]["temp"],
                "conditions": slot["weather"][0]["description"],
                "rain_prob": slot.get("pop", 0),
                "wind_speed": slot["wind"]["speed"],
            }
            for slot in f.get("list", [])[:8]  # next 24h in 3h steps
        ]
        return {
            "available": True,
            "current": {
                "temp": c["main"]["temp"],
                "feels_like": c["main"]["feels_like"],
                "humidity": c["main"]["humidity"],
                "conditions": c["weather"][0]["description"],
                "wind_speed": c["wind"]["speed"],
            },
            "forecast_next_24h": next_slots,
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


async def get_aqi(lat: float, lon: float, client: httpx.AsyncClient) -> dict:
    # OpenWeatherMap Air Pollution API — free tier, same key, no separate signup needed
    try:
        resp = await client.get(
            "https://api.openweathermap.org/data/2.5/air_pollution",
            params={"lat": lat, "lon": lon, "appid": OPENWEATHER_API_KEY},
        )
        resp.raise_for_status()
        data = resp.json()
        item = data["list"][0]
        aqi_scale = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}
        return {
            "available": True,
            "aqi_index": item["main"]["aqi"],  # 1-5 scale
            "aqi_label": aqi_scale.get(item["main"]["aqi"], "Unknown"),
            "pm2_5": item["components"].get("pm2_5"),
            "pm10": item["components"].get("pm10"),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def get_disaster_alerts(lat: float, lon: float, client: httpx.AsyncClient, radius_km: float = 400) -> dict:
    # GDACS global disaster alert RSS feed — free, no API key required
    ns = {"geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}
    try:
        resp = await client.get("https://www.gdacs.org/xml/rss.xml", timeout=10)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
        nearby = []
        for item in root.iter("item"):
            lat_el = item.find("geo:lat", ns)
            lon_el = item.find("geo:long", ns)
            if lat_el is None or lon_el is None:
                continue
            try:
                ilat, ilon = float(lat_el.text), float(lon_el.text)
            except (TypeError, ValueError):
                continue
            dist = _haversine_km(lat, lon, ilat, ilon)
            if dist <= radius_km:
                title = item.findtext("title", default="")
                description = item.findtext("description", default="")
                nearby.append({
                    "title": title,
                    "description": description[:300],
                    "distance_km": round(dist, 1),
                })
        nearby.sort(key=lambda x: x["distance_km"])
        return {"available": True, "alerts": nearby[:5]}
    except Exception as e:
        return {"available": False, "error": str(e), "alerts": []}


# ---------------------------------------------------------------------------
# Reasoning layer
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are SafeStep, a risk-assessment assistant that helps people in India \
decide whether a planned outdoor activity is safe to go ahead with, given live weather, \
air-quality, and disaster-alert data.

You will be given: a location, an activity, live weather data, live air quality data, and \
any nearby disaster alerts. Reason over ALL of these signals together, weighted by how much \
each one matters for the stated activity (e.g. rain matters more for a wedding than a factory \
shift; AQI matters more for outdoor physical exertion; heat matters more for farming/manual labor).

Respond with ONLY a valid JSON object, no other text, no markdown fences, matching this shape:
{
  "verdict": "PROCEED" | "CAUTION" | "AVOID",
  "headline_reason": "one plain-language sentence, max ~20 words",
  "details": [
    "short bullet explaining a specific factor and why it matters, plain language",
    "..."
  ],
  "risk_score": <integer 0-100, higher = riskier>
}

Keep language plain and specific to the activity — avoid generic disclaimers. If data sources \
are unavailable, reason with what you have and say so briefly in details rather than refusing."""


async def get_verdict(location_info: dict, activity: str, when: Optional[str],
                       weather: dict, aqi: dict, alerts: dict) -> dict:
    if not nim_client:
        raise HTTPException(status_code=500, detail="NVIDIA_API_KEY not configured on server")

    user_payload = {
        "location": f"{location_info['name']}, {location_info.get('state', '')} {location_info.get('country', '')}".strip(),
        "activity": activity,
        "when": when or "not specified, assume soon/today",
        "weather": weather,
        "air_quality": aqi,
        "disaster_alerts": alerts,
    }

    completion = nim_client.chat.completions.create(
        model="z-ai/glm-5.2",
        max_tokens=800,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload)},
        ],
        temperature=0.3,
    )

    raw_text = completion.choices[0].message.content.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        raw_text = raw_text.replace("json\n", "", 1)

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {
            "verdict": "CAUTION",
            "headline_reason": "Could not fully parse risk assessment — please review raw data below.",
            "details": [raw_text[:400]],
            "risk_score": 50,
        }


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "openweather_configured": bool(OPENWEATHER_API_KEY),
        "nvidia_nim_configured": bool(NVIDIA_API_KEY),
    }


@app.post("/assess")
async def assess(req: AssessRequest):
    if not OPENWEATHER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENWEATHER_API_KEY not configured on server")

    async with httpx.AsyncClient() as client:
        location_info = await geocode(req.location, client)
        lat, lon = location_info["lat"], location_info["lon"]

        weather, aqi, alerts = await asyncio.gather(
            get_weather(lat, lon, client),
            get_aqi(lat, lon, client),
            get_disaster_alerts(lat, lon, client),
        )

    verdict = await get_verdict(location_info, req.activity, req.when, weather, aqi, alerts)

    return {
        "location": location_info,
        "verdict": verdict,
        "raw_data": {
            "weather": weather,
            "air_quality": aqi,
            "disaster_alerts": alerts,
        },
    }
