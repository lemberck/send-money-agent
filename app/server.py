"""FastAPI server wrapping the Send Money Agent."""

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.sessions import InMemorySessionService, DatabaseSessionService
from google.genai import types

from send_money_agent.agent import root_agent
from send_money_agent.data import SUBMISSION_DELAY

import app.logging_config  # noqa: F401


def _extract_text(event) -> str | None:
    """Extract visible response text from an ADK event, filtering out thinking and tool parts."""
    if not event.content or not event.content.parts:
        return None
    # Skip events containing function calls/responses (tool invocations)
    if any(
        getattr(p, "function_call", None) is not None
        or getattr(p, "function_response", None) is not None
        for p in event.content.parts
    ):
        return None
    texts = []
    for part in event.content.parts:
        # Filter out model reasoning/thinking (Part.thought flag set by ADK's LiteLLM layer)
        if getattr(part, "thought", False):
            logger.debug("Filtered thought part: {}...", (part.text or "")[:80])
            continue
        if part.text:
            texts.append(part.text)
    return "".join(texts) if texts else None

APP_NAME = "send_money"
MAX_MESSAGE_LENGTH = 1000

run_config = RunConfig(streaming_mode=StreamingMode.SSE)

session_service = None
runner = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_service, runner
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        logger.info("Using DatabaseSessionService: {}", db_url)
        session_service = DatabaseSessionService(db_url=db_url)
    else:
        logger.info("Using InMemorySessionService")
        session_service = InMemorySessionService()

    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )
    logger.info("Send Money Agent runner started")
    yield
    logger.info("Shutting down")


async def _auto_submit(user_id: str, session_id: str, confirmed_at: float, reference: str):
    """Background task: transition confirmed → submitted after SUBMISSION_DELAY seconds."""
    await asyncio.sleep(SUBMISSION_DELAY)
    try:
        session = await session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if not session:
            logger.warning("auto_submit: session {} not found", session_id)
            return
        state = session.state

        # Always update the history entry for this reference
        history = list(state.get("transfer_history") or [])
        history_updated = False
        for entry in history:
            if entry.get("reference") == reference and entry.get("status") == "confirmed":
                entry["status"] = "submitted"
                history_updated = True
                break

        # Also update current transfer_status if it still matches this confirmation
        state_delta = {"transfer_history": history} if history_updated else {}
        if state.get("transfer_status") == "confirmed" and state.get("transfer_confirmed_at") == confirmed_at:
            state_delta["transfer_status"] = "submitted"

        if not state_delta:
            logger.info("auto_submit: session {} skipped for {} (already processed)", session_id, reference)
            return

        await session_service.append_event(
            session=session,
            event=Event(
                author="system",
                actions=EventActions(state_delta=state_delta),
            ),
        )
        logger.info("auto_submit: session {} ref {} transitioned to submitted", session_id, reference)
    except Exception as e:
        logger.error("auto_submit error for session {}: {}", session_id, e)


app = FastAPI(title="Send Money Agent", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class ChatRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str
    message: str


class CreateSessionResponse(BaseModel):
    session_id: str
    user_id: str


@app.get("/")
async def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/sessions", response_model=CreateSessionResponse)
async def create_session(user_id: str = "default_user"):
    session = await session_service.create_session(
        app_name=APP_NAME, user_id=user_id
    )
    logger.info("Created session {} for user {}", session.id, user_id)
    return CreateSessionResponse(session_id=session.id, user_id=user_id)


@app.get("/sessions/{session_id}/state")
async def get_session_state(session_id: str, user_id: str = "default_user"):
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "state": dict(session.state)}


@app.post("/chat")
async def chat(request: ChatRequest):
    """Streaming chat via Server-Sent Events. Always streams."""
    if not runner:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    logger.info("Chat: user={} session={} msg={}", request.user_id, request.session_id, request.message[:80])

    if len(request.message) > MAX_MESSAGE_LENGTH:
        logger.warning("Message too long: {} chars from user={}", len(request.message), request.user_id)
        request.message = (
            "[SYSTEM: The user sent a very long message. Do NOT process it. "
            "Kindly ask them to rephrase with a shorter message, focusing only on "
            "the information you still need for the transfer, one transfer at a time.]"
        )

    async def event_generator():
        has_streamed = False
        full_response_parts = []
        try:
            async for event in runner.run_async(
                user_id=request.user_id,
                session_id=request.session_id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=request.message)]
                ),
                run_config=run_config,
            ):
                # Notify user immediately when convert_currency is about to run
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        fc = getattr(part, "function_call", None)
                        if fc and fc.name == "convert_currency":
                            yield {"event": "token", "data": json.dumps({"text": "Converting to USD, one moment...\n\n"})}
                            has_streamed = True
                            break

                # Emit intermediate state updates when tools change state
                if event.actions and event.actions.state_delta:
                    session = await session_service.get_session(
                        app_name=APP_NAME, user_id=request.user_id, session_id=request.session_id
                    )
                    if session:
                        yield {"event": "state", "data": json.dumps({"state": dict(session.state)})}

                # Skip the final accumulated response if we already streamed tokens
                if event.is_final_response() and has_streamed:
                    continue
                text = _extract_text(event)
                if text:
                    has_streamed = True
                    full_response_parts.append(text)
                    logger.debug("Agent token: {}", text[:80])
                    yield {"event": "token", "data": json.dumps({"text": text})}
        except Exception as e:
            logger.error("Agent error: {}", e)
            yield {"event": "token", "data": json.dumps({"text": "Sorry, something went wrong. Please start a new session."})}

        if full_response_parts:
            logger.info("Agent full response: {}", "".join(full_response_parts)[:500])

        session = await session_service.get_session(
            app_name=APP_NAME, user_id=request.user_id, session_id=request.session_id
        )
        state = dict(session.state) if session else {}
        yield {"event": "state", "data": json.dumps({"state": state})}

        if state.get("transfer_status") == "confirmed" and state.get("transfer_confirmed_at"):
            asyncio.create_task(_auto_submit(
                request.user_id, request.session_id,
                state["transfer_confirmed_at"], state.get("transfer_reference", ""),
            ))

        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())
