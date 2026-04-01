import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

from services.analyzer import analyze
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    scheduler.add_job(prefetch_popular_locations, "interval", minutes=5)
    scheduler.start()
    logger.info("Scheduler started: pre-fetching every 5 minutes")
    # Run initial pre-fetch
    await prefetch_popular_locations()
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


@app.get("/health")
async def health_check():
    return {"status": "ok"}
