"""Current conditions and a 5-day forecast from the public Open-Meteo endpoint.

The viewer calls this when a pin is clicked. Nothing is written to SQLite: the viewer caches the
returned dict for half an hour. The host is api.open-meteo.com, the non-commercial API; the customer
API is refused. One rate-limited client, the same way every other provider is called.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from eww import config
from eww import http as http_mod
from eww import ratelimit

ATTRIBUTION = config.OPEN_METEO_ATTRIBUTION
CACHE_TTL_S = config.OPEN_METEO_CACHE_TTL_S
_HOST = "api.open-meteo.com"

# WMO weather interpretation codes (Open-Meteo). Unknown codes stay readable as "code N".
_CONDITIONS = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Freezing drizzle",
    61: "Slight rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Freezing rain",
    71: "Slight snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def conditions(code) -> str:
    if code is None:
        return "Not reported"
    try:
        number = int(code)
    except (TypeError, ValueError):
        return "Not reported"
    return _CONDITIONS.get(number, f"code {number}")


def _round(value, digits: int = 1):
    if value is None:
        return None
    return round(float(value), digits)


def forecast(latitude: float, longitude: float, *, http=None) -> dict:
    """Current conditions and five daily rows for one pin. Raises if the host is not the public API."""
    host = urlsplit(config.OPEN_METEO_URL).hostname
    if host != _HOST:
        raise ValueError(f"Open-Meteo host must be {_HOST}")
    limiter = ratelimit.MinInterval(config.OPEN_METEO_MIN_INTERVAL_S)
    window = ratelimit.SlidingWindow(config.OPEN_METEO_PER_MINUTE, 60, name="open-meteo")
    window.preload(ratelimit.recent_call_ages("open-meteo", 60))
    provider = http_mod.Provider(
        "open-meteo",
        limiters=[limiter, window],
        http=http,
        timeout=config.OPEN_METEO_TIMEOUT_S,
        retries=1,
    )
    try:
        status, body = provider.get_json(
            config.OPEN_METEO_URL,
            {
                "latitude": round(float(latitude), 4),
                "longitude": round(float(longitude), 4),
                "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                "forecast_days": config.OPEN_METEO_FORECAST_DAYS,
                "timezone": "auto",
            },
        )
    finally:
        provider.close()
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"open-meteo status {status}")
    current = body.get("current") or {}
    daily = body.get("daily") or {}
    days = daily.get("time") or []
    rows = []
    for index, day in enumerate(days[: config.OPEN_METEO_FORECAST_DAYS]):
        rows.append(
            {
                "date": day,
                "high_c": _round(_at(daily, "temperature_2m_max", index)),
                "low_c": _round(_at(daily, "temperature_2m_min", index)),
                "precipitation_mm": _round(_at(daily, "precipitation_sum", index)),
                "conditions": conditions(_at(daily, "weather_code", index)),
            }
        )
    return {
        "attribution": ATTRIBUTION,
        "current": {
            "temperature_c": _round(current.get("temperature_2m")),
            "precipitation_mm": _round(current.get("precipitation")),
            "wind_kmh": _round(current.get("wind_speed_10m")),
            "conditions": conditions(current.get("weather_code")),
            "observed_at": current.get("time"),
        },
        "daily": rows,
    }


def _at(daily: dict, key: str, index: int):
    values = daily.get(key) or []
    if index >= len(values):
        return None
    return values[index]
