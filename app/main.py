"""FastAPI app factory + entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import calls as calls_router
from app.api import scenarios as scenarios_router
from app.api import webhooks as webhooks_router
from app.api.deps import (
    get_app_settings,
    get_conversation_engine,
    get_outcome_recorder,
    get_store,
    install_singletons,
)
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.providers.factory import build_stt, build_tts
from app.ws.media_stream import MediaStreamBridge


WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("startup")

    missing = settings.validate_required_keys()
    if missing:
        log.warning(
            "config.missing_keys",
            missing=missing,
            hint="App will start, but call dispatch will fail until these are set in .env",
        )

    install_singletons(app, settings)
    log.info(
        "app.started",
        public_base_url=settings.public_base_url,
        llm_provider=settings.llm_provider,
        stt_provider=settings.stt_provider,
        tts_provider=settings.tts_provider,
        telephony_provider=settings.telephony_provider,
        streaming_mode=settings.streaming_mode,
    )
    yield
    log.info("app.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Voice AI Agent",
        version="0.1.0",
        description=(
            "Outbound voice AI agent. FastAPI orchestrates Twilio + Deepgram "
            "STT/TTS + Groq LLM through swappable provider interfaces."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(calls_router.router)
    app.include_router(scenarios_router.router)
    app.include_router(webhooks_router.router)

    @app.websocket("/ws/media/{call_id}")
    async def media_ws(
        websocket: WebSocket,
        call_id: str,
    ) -> None:
        # Each call gets a fresh STT and TTS connection (stateful WS).
        settings = get_settings()
        store = websocket.app.state.session_store
        session = await store.get(call_id)
        if not session:
            await websocket.close(code=4404)
            return
        stt = build_stt(settings)
        tts = build_tts(settings)
        bridge = MediaStreamBridge(
            ws=websocket,
            session=session,
            store=store,
            stt=stt,
            tts=tts,
            engine=websocket.app.state.engine,
            outcome_recorder=websocket.app.state.outcome_recorder,
            settings=settings,
        )
        await bridge.run()

    @app.get("/healthz")
    async def healthz(settings=Depends(get_app_settings)) -> dict:
        return {
            "ok": True,
            "missing_config": settings.validate_required_keys(),
            "providers": {
                "stt": settings.stt_provider,
                "llm": settings.llm_provider,
                "tts": settings.tts_provider,
                "telephony": settings.telephony_provider,
            },
        }

    # Static UI
    if WEB_DIR.exists():
        app.mount(
            "/static", StaticFiles(directory=str(WEB_DIR)), name="static"
        )

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            index_path = WEB_DIR / "index.html"
            if not index_path.exists():
                raise HTTPException(status_code=404, detail="UI not built")
            return FileResponse(index_path)

    return app


app = create_app()
