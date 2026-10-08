from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import files
from app.database import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Geospatial File Measurement API", version="1.0.0", lifespan=lifespan)
app.include_router(files.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
