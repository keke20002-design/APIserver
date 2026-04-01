import logging
from contextlib import asynccontextmanager
from datetime import date, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from services.analyzer import analyze
from services.kbo_schedule import fetch_kbo_schedule
from services.restaurant import search_restaurants
from utils.cache import cache

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Locations to pre-fetch on schedule
POPULAR_LOCATIONS = ["잠실", "고척"]

scheduler = AsyncIOScheduler()


async def prefetch_popular_locations():
    """Background job: pre-fetch parking data for popular locations."""
    for location in POPULAR_LOCATIONS:
        try:
            result = await analyze(location)
            cache.set(location, result)
            logger.info("Pre-fetched parking data for %s", location)
        except Exception as e:
            logger.error("Failed to pre-fetch %s: %s", location, e)


async def prefetch_game_schedule():
    """Background job: pre-fetch today's KBO game schedule."""
    try:
        games = await fetch_kbo_schedule()
        result = {
            "date": date.today().isoformat(),
            "total_games": len(games),
            "games": games,
        }
        cache.set("games:today", result)
        logger.info("Pre-fetched KBO schedule: %d games", len(games))
    except Exception as e:
        logger.error("Failed to pre-fetch KBO schedule: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    scheduler.add_job(prefetch_popular_locations, "interval", minutes=5)
    scheduler.add_job(prefetch_game_schedule, "interval", minutes=10)
    scheduler.start()
    logger.info("Scheduler started: parking every 5min, games every 10min")
    # Run initial pre-fetch
    await prefetch_popular_locations()
    await prefetch_game_schedule()
    yield
    # Shutdown
    scheduler.shutdown()
    logger.info("Scheduler shut down")


app = FastAPI(
    title="Parking Congestion API",
    description="야구장 주변 주차 혼잡도 분석 API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/parking-status")
async def get_parking_status(
    location: str = Query(..., description="분석할 위치 (예: 잠실, 고척)"),
):
    """Analyze parking congestion around the given location."""
    if not location.strip():
        raise HTTPException(status_code=400, detail="location parameter is required")

    # Check cache
    cached = cache.get(location)
    if cached is not None:
        logger.info("Cache hit for '%s'", location)
        return cached

    # Fetch and analyze
    try:
        result = await analyze(location)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("Analysis failed for '%s': %s", location, e)
        raise HTTPException(
            status_code=502, detail="Failed to fetch data from Naver API"
        )

    cache.set(location, result)
    return result


@app.get("/games")
async def get_game_schedule(
    date_str: str | None = Query(
        None, alias="date", description="조회 날짜 (YYYY-MM-DD, 기본: 오늘)"
    ),
):
    """Get today's KBO game schedule with ticket links."""
    cache_key = f"games:{date_str or 'today'}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for games '%s'", date_str)
        return cached

    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
            )

    try:
        games = await fetch_kbo_schedule(target_date)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    result = {
        "date": (target_date or date.today()).isoformat(),
        "total_games": len(games),
        "games": games,
    }
    cache.set(cache_key, result)
    return result


@app.get("/restaurants")
async def get_restaurants(
    stadium: str = Query(
        ..., description="구장 위치 (예: 잠실, 고척, 사직)"
    ),
):
    """Search restaurants near a stadium with review summary."""
    cache_key = f"restaurants:{stadium}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for restaurants '%s'", stadium)
        return cached

    if not stadium.strip():
        raise HTTPException(status_code=400, detail="stadium parameter is required")

    try:
        result = await search_restaurants(stadium)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("Restaurant search failed for '%s': %s", stadium, e)
        raise HTTPException(
            status_code=502, detail="Failed to fetch restaurant data"
        )

    cache.set(cache_key, result)
    return result


@app.get("/health")
async def health_check():
    return {"status": "ok"}
