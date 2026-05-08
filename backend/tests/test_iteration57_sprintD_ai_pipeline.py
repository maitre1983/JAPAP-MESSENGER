"""Iteration 57 — Sprint D end-to-end AI pipeline isolation test.

Bypasses LiveKit Egress entirely. Uploads a real ~8s French speech mp3 to R2,
inserts a fake call_sessions + call_recordings row in postgres, then calls
run_call_ai_pipeline() directly to exercise:

    R2 presigned GET -> Whisper (OpenAI) transcript -> Claude Sonnet summary
        -> persistence in call_summaries with status='ready'.

Also verifies GET /api/calls/{session_id}/summary returns the populated row.
"""
import os
import sys
import uuid
import asyncio
import pathlib
import pytest
import requests

# Ensure backend modules are importable + env loaded
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402
load_dotenv("/app/backend/.env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
FIXTURE = pathlib.Path("/app/backend/tests/fixtures/sample_audio.mp3")

BOB = {"email": "bob@japap.com", "password": "Test1234!"}


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def bob_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json=BOB, timeout=15)
    assert r.status_code == 200, r.text
    return s, r.json()["user"]["user_id"]


@pytest.fixture(scope="module")
def uploaded_recording(bob_session, event_loop):
    """Upload sample mp3 to R2 + create DB rows. Returns (session_id, recording_id, summary_id, key)."""
    assert FIXTURE.exists(), f"Missing fixture: {FIXTURE}"
    _, bob_uid = bob_session

    async def _setup():
        from services.r2_storage_service import get_r2_config, build_recording_key
        from database import get_pool
        cfg = await get_r2_config()
        session_id = f"sess_test57{uuid.uuid4().hex[:8]}"
        recording_id = f"rec_test57{uuid.uuid4().hex[:8]}"
        summary_id = f"sum_test57{uuid.uuid4().hex[:8]}"
        # Use mp3 ext so transcribe_audio() picks .mp3 tempfile
        key = f"recordings/test/{session_id}/{recording_id}.mp3"

        # Upload to R2 via boto3
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=cfg["endpoint"],
            aws_access_key_id=cfg["access_key"],
            aws_secret_access_key=cfg["secret"],
            region_name="auto",
        )
        with open(FIXTURE, "rb") as fh:
            s3.put_object(Bucket=cfg["bucket"], Key=key, Body=fh.read(), ContentType="audio/mpeg")

        # Create DB rows
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO call_sessions
                  (session_id, room_name, mode, kind, host_user_id, status, max_participants, metadata)
                VALUES ($1, $2, 'audio', 'p2p', $3, 'ended', 2, '{}'::jsonb)
            """, session_id, f"japap_{session_id}", bob_uid)
            await conn.execute("""
                INSERT INTO call_participants (session_id, user_id, role, joined_at, left_at)
                VALUES ($1, $2, 'host', now(), now())
                ON CONFLICT DO NOTHING
            """, session_id, bob_uid)
            await conn.execute("""
                INSERT INTO call_recordings
                  (recording_id, session_id, egress_id, status,
                   storage_provider, storage_bucket, storage_key, mime_type, public_url)
                VALUES ($1, $2, 'fake-egress', 'ready', 'r2', $3, $4, 'audio/mpeg', $5)
            """, recording_id, session_id, cfg["bucket"], key,
                f"{cfg['endpoint']}/{cfg['bucket']}/{key}")
            await conn.execute("""
                INSERT INTO call_summaries (summary_id, session_id, recording_id, status)
                VALUES ($1, $2, $3, 'pending')
            """, summary_id, session_id, recording_id)
        return session_id, recording_id, summary_id, key

    session_id, recording_id, summary_id, key = event_loop.run_until_complete(_setup())
    yield session_id, recording_id, summary_id, key

    # Teardown
    async def _cleanup():
        from services.r2_storage_service import get_r2_config
        from database import get_pool
        cfg = await get_r2_config()
        try:
            import boto3
            s3 = boto3.client(
                "s3",
                endpoint_url=cfg["endpoint"],
                aws_access_key_id=cfg["access_key"],
                aws_secret_access_key=cfg["secret"],
                region_name="auto",
            )
            s3.delete_object(Bucket=cfg["bucket"], Key=key)
        except Exception:
            pass
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM call_summaries WHERE session_id=$1", session_id)
            await conn.execute("DELETE FROM call_recordings WHERE session_id=$1", session_id)
            await conn.execute("DELETE FROM call_participants WHERE session_id=$1", session_id)
            await conn.execute("DELETE FROM call_sessions WHERE session_id=$1", session_id)

    event_loop.run_until_complete(_cleanup())


def test_r2_health(bob_session):
    """Sanity: R2 must be reachable from the API perspective (admin only)."""
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@japap.com", "password": "JapapAdmin2024!"}, timeout=15)
    assert r.status_code == 200
    h = s.get(f"{BASE_URL}/api/calls/test-r2", timeout=15)
    body = h.json()
    assert body.get("ok") is True, body
    assert body.get("bucket") == "japap-recordings"


def test_run_ai_pipeline_end_to_end(uploaded_recording, event_loop):
    """Whisper + Claude pipeline must produce a 'ready' summary with non-empty fields."""
    session_id, recording_id, summary_id, _ = uploaded_recording

    async def _run():
        from services.call_ai_service import run_call_ai_pipeline
        return await run_call_ai_pipeline(recording_id=recording_id, summary_id=summary_id)

    result = event_loop.run_until_complete(_run())
    assert result.get("status") == "ready", result
    assert result.get("summary_id") == summary_id

    # Verify DB row populated
    async def _fetch():
        from database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM call_summaries WHERE summary_id=$1", summary_id)

    row = event_loop.run_until_complete(_fetch())
    assert row is not None
    assert row["status"] == "ready"
    assert (row["transcript"] or "").strip(), "Whisper transcript empty"
    assert (row["summary"] or "").strip(), "Claude summary empty"
    assert row["model"] == "claude-sonnet-4-5"
    print(f"Transcript: {row['transcript'][:200]}")
    print(f"Summary: {row['summary'][:200]}")


def test_get_summary_endpoint_returns_ready(bob_session, uploaded_recording):
    """GET /api/calls/{id}/summary must return the populated row with key_points/decisions/action_items as lists."""
    s, _ = bob_session
    session_id, _, _, _ = uploaded_recording
    r = s.get(f"{BASE_URL}/api/calls/{session_id}/summary", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready", body
    assert isinstance(body.get("key_points"), list)
    assert isinstance(body.get("decisions"), list)
    assert isinstance(body.get("action_items"), list)
    assert body.get("transcript")
    assert body.get("summary")
    assert body.get("recording") and body["recording"].get("public_url")
