import httpx

from src.core.config import get_settings

settings = get_settings()


def _get_tomtom_key() -> str:
    key = getattr(settings, "tomtom_api_key", None)
    if not key:
        raise RuntimeError("Missing TOMTOM_API_KEY in .env")
    return key

def _raise_tomtom_error(response: httpx.Response, api_key: str, api_name: str) -> None:
    if response.status_code < 400:
        return

    safe_url = str(response.request.url).replace(api_key, "***MASKED***")
    safe_body = response.text.replace(api_key, "***MASKED***")

    print(
        f"[{api_name} ERROR]",
        response.status_code,
        safe_url,
        safe_body,
    )

    raise RuntimeError(
        f"{api_name} request failed with status {response.status_code}"
    )

async def get_flow_segment(lat: float, lon: float, zoom: int = 14) -> dict:
    key = _get_tomtom_key()

    url = (
        f"https://api.tomtom.com/traffic/services/4/"
        f"flowSegmentData/relative0/{zoom}/json"
    )

    params = {
        "point": f"{lat},{lon}",
        "unit": "KMPH",
        "openLr": "false",
        "key": key,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url, params=params)
        _raise_tomtom_error(response, key, "TomTom Flow Segment API")
        return response.json()


async def get_incidents_district_1() -> dict:
    key = _get_tomtom_key()

    # bbox Quận 1 ước lượng: west,south,east,north
    bbox = "106.672782,10.753522,106.710120,10.793739"

    url = "https://api.tomtom.com/traffic/services/5/incidentDetails"

    params = {
        "key": key,
        "bbox": bbox,
        "fields": "{incidents{type,geometry{type,coordinates},properties{iconCategory,magnitudeOfDelay,events{description,code},startTime,endTime,from,to,length,delay,roadNumbers}}}",
        "language": "en-GB",
        "categoryFilter": "0,1,2,3,4,5,6,7,8,9,10,11,14",
        "timeValidityFilter": "present",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)
        _raise_tomtom_error(response, key, "TomTom Incident API")
        return response.json()