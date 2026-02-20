"""Balatro Run Viewer - FastAPI backend."""

import json
import os
import uuid
from pathlib import Path

import asyncpg
import aiofiles
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
from contextlib import asynccontextmanager

# Config
NEON_CONFIG = Path(__file__).parent.parent.parent.parent / "data" / "neon-config.json"
SCREENSHOT_DIR = Path("/home/ubuntu/balatro-screenshots")
JOKER_DATA = Path(__file__).parent.parent / "data" / "jokers.json"
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

_joker_catalog: list[dict] | None = None


def _load_joker_catalog() -> list[dict]:
    global _joker_catalog
    if _joker_catalog is None:
        try:
            with open(JOKER_DATA) as f:
                _joker_catalog = json.load(f)
        except FileNotFoundError:
            _joker_catalog = []
    return _joker_catalog

db_pool: asyncpg.Pool | None = None


def get_database_url() -> str:
    with open(NEON_CONFIG) as f:
        return json.load(f)["database_url"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    db_pool = await asyncpg.create_pool(get_database_url(), min_size=2, max_size=10)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    yield
    if db_pool:
        await db_pool.close()


app = FastAPI(title="Balatro Run Viewer", lifespan=lifespan)

# Serve screenshots as static files
app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOT_DIR)), name="screenshots")


# ── Runs ──────────────────────────────────────────────────────────────

@app.get("/api/runs")
async def list_runs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    deck: str | None = None,
    stake: str | None = None,
    won: bool | None = None,
    sort: str = Query("played_at", pattern="^(played_at|final_ante|final_score|created_at)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    """List runs with pagination and filters."""
    conditions = []
    params = []
    idx = 1

    if deck:
        conditions.append(f"deck = ${idx}")
        params.append(deck)
        idx += 1
    if stake:
        conditions.append(f"stake = ${idx}")
        params.append(stake)
        idx += 1
    if won is not None:
        conditions.append(f"won = ${idx}")
        params.append(won)
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Count
    count_row = await db_pool.fetchrow(f"SELECT COUNT(*) FROM balatro_runs {where}", *params)
    total = count_row["count"]

    # Fetch
    offset = (page - 1) * per_page
    rows = await db_pool.fetch(
        f"""SELECT r.*, 
                   s.name AS strategy_name, s.id AS strategy_sid,
                   (SELECT COUNT(*) FROM balatro_screenshots sc WHERE sc.run_id = r.id) AS screenshot_count
            FROM balatro_runs r
            LEFT JOIN balatro_strategies s ON r.strategy_id = s.id
            {where}
            ORDER BY {sort} {order}
            LIMIT ${idx} OFFSET ${idx + 1}""",
        *params, per_page, offset,
    )

    return {
        "runs": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total else 0,
    }


@app.get("/api/runs/by-code/{run_code}")
async def get_run_by_code(run_code: str):
    """Lookup a run by run_code and return full detail."""
    run = await db_pool.fetchrow("SELECT id FROM balatro_runs WHERE run_code = $1", run_code)
    if not run:
        raise HTTPException(404, "Run not found")
    return await get_run(run["id"])


@app.get("/api/runs/{run_id}")
async def get_run(run_id: int):
    """Get full run detail with jokers, rounds, screenshots, tags."""
    run = await db_pool.fetchrow("SELECT * FROM balatro_runs WHERE id = $1", run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    jokers = await db_pool.fetch(
        "SELECT * FROM balatro_jokers WHERE run_id = $1 ORDER BY position", run_id
    )
    rounds = await db_pool.fetch(
        "SELECT * FROM balatro_rounds WHERE run_id = $1 ORDER BY ante, blind_type", run_id
    )
    screenshots = await db_pool.fetch(
        "SELECT * FROM balatro_screenshots WHERE run_id = $1 ORDER BY created_at", run_id
    )
    tags = await db_pool.fetch(
        "SELECT * FROM balatro_tags WHERE run_id = $1 ORDER BY ante", run_id
    )

    # Strategy info
    strategy = None
    if run.get("strategy_id"):
        srow = await db_pool.fetchrow("SELECT * FROM balatro_strategies WHERE id = $1", run["strategy_id"])
        if srow:
            strategy = dict(srow)

    return {
        "run": dict(run),
        "jokers": [dict(j) for j in jokers],
        "rounds": [dict(r) for r in rounds],
        "screenshots": [dict(s) for s in screenshots],
        "tags": [dict(t) for t in tags],
        "strategy": strategy,
    }


@app.post("/api/runs")
async def create_run(
    seed: str | None = Form(None),
    deck: str = Form("Red Deck"),
    stake: str = Form("White"),
    final_ante: int = Form(1),
    final_score: int | None = Form(None),
    won: bool = Form(False),
    endless_ante: int | None = Form(None),
    notes: str | None = Form(None),
    played_at: str | None = Form(None),
):
    """Create a new run."""
    row = await db_pool.fetchrow(
        """INSERT INTO balatro_runs (seed, deck, stake, final_ante, final_score, won, endless_ante, notes, played_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, COALESCE($9::timestamptz, NOW()))
           RETURNING *""",
        seed, deck, stake, final_ante, final_score, won, endless_ante, notes, played_at,
    )
    return {"run": dict(row)}


@app.put("/api/runs/{run_id}")
async def update_run(run_id: int):
    """Update a run (accepts JSON body)."""
    # We'll handle this via JSON since it's easier for updates
    raise HTTPException(501, "Use PATCH endpoint")


@app.patch("/api/runs/{run_id}")
async def patch_run(run_id: int, body: dict):
    """Patch run fields."""
    allowed = {"seed", "deck", "stake", "final_ante", "final_score", "won", "endless_ante", "notes", "played_at"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        raise HTTPException(400, "No valid fields to update")

    sets = []
    params = []
    for i, (k, v) in enumerate(fields.items(), 1):
        sets.append(f"{k} = ${i}")
        params.append(v)
    params.append(run_id)

    row = await db_pool.fetchrow(
        f"UPDATE balatro_runs SET {', '.join(sets)} WHERE id = ${len(params)} RETURNING *",
        *params,
    )
    if not row:
        raise HTTPException(404, "Run not found")
    return {"run": dict(row)}


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: int):
    """Delete a run and its screenshots from disk."""
    run = await db_pool.fetchrow("SELECT id FROM balatro_runs WHERE id = $1", run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    # Delete screenshot files
    screenshots = await db_pool.fetch(
        "SELECT filename FROM balatro_screenshots WHERE run_id = $1", run_id
    )
    for s in screenshots:
        fpath = SCREENSHOT_DIR / s["filename"]
        if fpath.exists():
            fpath.unlink()

    # Cascade delete handles DB rows
    await db_pool.execute("DELETE FROM balatro_runs WHERE id = $1", run_id)

    # Clean up empty run directory
    run_dir = SCREENSHOT_DIR / str(run_id)
    if run_dir.exists() and not any(run_dir.iterdir()):
        run_dir.rmdir()

    return {"deleted": True}


# ── Jokers ────────────────────────────────────────────────────────────

@app.post("/api/runs/{run_id}/jokers")
async def add_joker(
    run_id: int,
    name: str = Form(...),
    position: int = Form(...),
    edition: str | None = Form(None),
    eternal: bool = Form(False),
    perishable: bool = Form(False),
    rental: bool = Form(False),
):
    """Add a joker to a run."""
    row = await db_pool.fetchrow(
        """INSERT INTO balatro_jokers (run_id, name, position, edition, eternal, perishable, rental)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
        run_id, name, position, edition, eternal, perishable, rental,
    )
    # Update joker count
    await db_pool.execute(
        "UPDATE balatro_runs SET joker_count = (SELECT COUNT(*) FROM balatro_jokers WHERE run_id = $1) WHERE id = $1",
        run_id,
    )
    return {"joker": dict(row)}


@app.post("/api/runs/{run_id}/jokers/batch")
async def add_jokers_batch(run_id: int, jokers: list[dict]):
    """Add multiple jokers at once."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            results = []
            for j in jokers:
                row = await conn.fetchrow(
                    """INSERT INTO balatro_jokers (run_id, name, position, edition, eternal, perishable, rental)
                       VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
                    run_id, j["name"], j["position"], j.get("edition"),
                    j.get("eternal", False), j.get("perishable", False), j.get("rental", False),
                )
                results.append(dict(row))
            await conn.execute(
                "UPDATE balatro_runs SET joker_count = (SELECT COUNT(*) FROM balatro_jokers WHERE run_id = $1) WHERE id = $1",
                run_id,
            )
    return {"jokers": results}


# ── Rounds ────────────────────────────────────────────────────────────

async def _sync_final_score(conn, run_id: int):
    """Update run's final_score to the max best_hand_score across all rounds."""
    await conn.execute(
        """UPDATE balatro_runs
           SET final_score = (SELECT MAX(best_hand_score) FROM balatro_rounds WHERE run_id = $1)
           WHERE id = $1""",
        run_id,
    )

@app.post("/api/runs/{run_id}/rounds")
async def add_round(
    run_id: int,
    ante: int = Form(...),
    blind_type: str = Form(...),
    boss_name: str | None = Form(None),
    target_score: int | None = Form(None),
    best_hand_score: int | None = Form(None),
    hands_played: int | None = Form(None),
    discards_used: int | None = Form(None),
    skipped: bool = Form(False),
    money_after: int | None = Form(None),
):
    """Add a round result."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO balatro_rounds 
                   (run_id, ante, blind_type, boss_name, target_score, best_hand_score, hands_played, discards_used, skipped, money_after)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *""",
                run_id, ante, blind_type, boss_name, target_score, best_hand_score,
                hands_played, discards_used, skipped, money_after,
            )
            await _sync_final_score(conn, run_id)
    return {"round": dict(row)}


@app.post("/api/runs/{run_id}/rounds/batch")
async def add_rounds_batch(run_id: int, rounds: list[dict]):
    """Add multiple rounds at once."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            results = []
            for r in rounds:
                row = await conn.fetchrow(
                    """INSERT INTO balatro_rounds 
                       (run_id, ante, blind_type, boss_name, target_score, best_hand_score, hands_played, discards_used, skipped, money_after)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *""",
                    run_id, r["ante"], r["blind_type"], r.get("boss_name"),
                    r.get("target_score"), r.get("best_hand_score"),
                    r.get("hands_played"), r.get("discards_used"),
                    r.get("skipped", False), r.get("money_after"),
                )
                results.append(dict(row))
            await _sync_final_score(conn, run_id)
    return {"rounds": results}


# ── Tags ──────────────────────────────────────────────────────────────

@app.post("/api/runs/{run_id}/tags")
async def add_tag(run_id: int, ante: int = Form(...), name: str = Form(...)):
    """Add a tag."""
    row = await db_pool.fetchrow(
        "INSERT INTO balatro_tags (run_id, ante, name) VALUES ($1, $2, $3) RETURNING *",
        run_id, ante, name,
    )
    return {"tag": dict(row)}


# ── Screenshots ───────────────────────────────────────────────────────

@app.post("/api/runs/{run_id}/screenshots")
async def upload_screenshot(
    run_id: int,
    file: UploadFile = File(...),
    round_id: int | None = Form(None),
    caption: str | None = Form(None),
):
    """Upload a screenshot for a run."""
    # Validate run exists
    run = await db_pool.fetchrow("SELECT id FROM balatro_runs WHERE id = $1", run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    # Validate extension
    ext = Path(file.filename).suffix.lower() if file.filename else ".png"
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type {ext} not allowed. Use: {ALLOWED_EXTENSIONS}")

    # Read and validate size
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(400, f"File too large. Max {MAX_UPLOAD_SIZE // 1024 // 1024}MB")

    # Save to disk
    run_dir = SCREENSHOT_DIR / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{run_id}/{uuid.uuid4().hex}{ext}"
    filepath = SCREENSHOT_DIR / filename

    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)

    # Try to get image dimensions
    width, height = None, None
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(content))
        width, height = img.size
    except Exception:
        pass

    # Save to DB
    row = await db_pool.fetchrow(
        """INSERT INTO balatro_screenshots (run_id, round_id, filename, original_name, caption, file_size, width, height)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *""",
        run_id, round_id, filename, file.filename, caption, len(content), width, height,
    )
    return {"screenshot": dict(row)}


@app.delete("/api/screenshots/{screenshot_id}")
async def delete_screenshot(screenshot_id: int):
    """Delete a screenshot."""
    row = await db_pool.fetchrow(
        "SELECT * FROM balatro_screenshots WHERE id = $1", screenshot_id
    )
    if not row:
        raise HTTPException(404, "Screenshot not found")

    # Delete file
    fpath = SCREENSHOT_DIR / row["filename"]
    if fpath.exists():
        fpath.unlink()

    await db_pool.execute("DELETE FROM balatro_screenshots WHERE id = $1", screenshot_id)
    return {"deleted": True}


# ── Stats ─────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    """Overall stats summary."""
    stats = await db_pool.fetchrow("""
        SELECT 
            COUNT(*) AS total_runs,
            COUNT(*) FILTER (WHERE won) AS wins,
            COUNT(*) FILTER (WHERE NOT won) AS losses,
            MAX(final_ante) AS highest_ante,
            MAX(final_score) AS highest_score,
            COUNT(DISTINCT deck) AS decks_used,
            COUNT(DISTINCT stake) AS stakes_played
        FROM balatro_runs
    """)
    return {"stats": dict(stats)}


# ── Joker Catalog ─────────────────────────────────────────────────────

@app.get("/api/jokers/catalog")
async def joker_catalog():
    """Return the full joker catalog with images and descriptions."""
    return {"jokers": _load_joker_catalog()}


@app.get("/api/jokers/lookup/{name}")
async def joker_lookup(name: str):
    """Lookup a joker by English name (case-insensitive)."""
    catalog = _load_joker_catalog()
    name_lower = name.lower().strip()
    for j in catalog:
        if j["name_en"].lower() == name_lower:
            return j
    raise HTTPException(404, f"Joker '{name}' not found in catalog")


# ── Health ────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """Health check."""
    try:
        await db_pool.fetchval("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return JSONResponse({"status": "error", "db": str(e)}, status_code=503)


# ── Server-rendered HTML pages ────────────────────────────────────────

STATIC_DIR = Path(__file__).parent.parent / "static"

def _base_css():
    """Shared CSS for all pages."""
    return """
:root{--bg:#1a1a2e;--surface:#16213e;--card:#0f3460;--accent:#e94560;--gold:#f5c518;--text:#eee;--muted:#aaa;--win:#4ade80;--loss:#f87171}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--gold);text-decoration:none}a:hover{text-decoration:underline}
.container{max-width:1400px;margin:0 auto;padding:1rem}
header{background:var(--surface);padding:1rem 0;border-bottom:2px solid var(--accent);margin-bottom:1.5rem}
header .container{display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1.5rem}header h1 span{color:var(--accent)}
.run-table{width:100%;border-collapse:collapse}
.run-table th{text-align:left;padding:.5rem .75rem;color:var(--muted);font-size:.8rem;text-transform:uppercase;border-bottom:1px solid #333}
.run-table td{padding:.6rem .75rem;border-bottom:1px solid #222}
.run-table tbody tr:hover{background:var(--surface);cursor:pointer}
.run-code{color:var(--gold);font-family:monospace;font-weight:bold}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:4px;font-size:.75rem;font-weight:600}
.badge.win{background:#166534;color:var(--win)}.badge.loss{background:#7f1d1d;color:var(--loss)}
.badge.running{background:#1e3a5f;color:#60a5fa;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.back-btn{display:inline-block;margin-bottom:1rem;padding:.4rem .8rem;background:var(--surface);border:1px solid #333;border-radius:6px;color:var(--text);font-size:.85rem}
.detail-header{background:var(--surface);padding:1.25rem;border-radius:12px;margin-bottom:1.5rem}
.detail-header h2{margin-bottom:.5rem;font-size:1.4rem}
.detail-stats{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.75rem}
.detail-stats .stat{background:var(--card);padding:.5rem .75rem;border-radius:8px;text-align:center;min-width:80px}
.detail-stats .stat .val{font-size:1.2rem;font-weight:bold;color:var(--gold)}
.detail-stats .stat .lbl{font-size:.7rem;color:var(--muted)}
.joker-grid{display:flex;gap:1.25rem;flex-wrap:wrap;margin-bottom:1.5rem}
.joker-card{display:flex;gap:1rem;background:var(--surface);padding:1rem;border-radius:12px;min-width:320px;max-width:480px;flex:1}
.joker-card img{width:96px;height:96px;object-fit:contain;flex-shrink:0}
.joker-card .joker-info{flex:1}
.joker-card .name-en{font-size:1.1rem;font-weight:600}.joker-card .name-zh{font-size:1rem;color:var(--gold);margin-top:3px}
.joker-card .effect{font-size:.9rem;color:var(--muted);margin-top:6px;line-height:1.4}
.feed{display:flex;flex-direction:column;gap:1.5rem}
.feed-entry{background:var(--surface);border-radius:12px;overflow:hidden}
.feed-entry .caption{padding:.75rem 1.25rem;color:#fff;font-size:1.25rem;line-height:1.6;font-weight:500}
.feed-entry .caption .source-tag{font-size:.85rem;padding:.2rem .5rem;border-radius:4px;font-weight:600;margin-left:.5rem;vertical-align:middle}
.feed-entry .caption .source-tag.rule{background:#1e3a5f;color:#60a5fa}
.feed-entry .caption .source-tag.llm{background:#3b1f5e;color:#c084fc}
.feed-entry img.screenshot{width:100%;display:block}
.score-bar{display:flex;align-items:center;gap:.75rem;padding:.4rem 1.25rem .6rem;font-size:1rem;font-family:monospace}
.score-est{color:var(--muted)}.score-arrow{color:#555}.score-act{color:var(--text);font-weight:600}
.score-err{padding:.15rem .4rem;border-radius:4px;font-size:.85rem;font-weight:600}
.score-err.good{background:#166534;color:var(--win)}.score-err.ok{background:#854d0e;color:#fbbf24}.score-err.bad{background:#7f1d1d;color:var(--loss)}
.section{margin-bottom:1.5rem}.section h3{margin-bottom:.75rem;font-size:1.1rem}
.blind-divider{padding:.75rem 1rem;font-size:1.1rem;font-weight:700;color:var(--gold);border-bottom:1px solid #333}
.detail-layout{display:flex;gap:1.5rem;align-items:flex-start}
.detail-main{flex:1;min-width:0}
.toc{position:sticky;top:1rem;width:200px;flex-shrink:0;background:var(--surface);border-radius:12px;padding:.75rem;max-height:calc(100vh - 2rem);overflow-y:auto}
.toc-title{font-size:.85rem;font-weight:600;color:var(--muted);text-transform:uppercase;margin-bottom:.5rem;padding-bottom:.5rem;border-bottom:1px solid #333}
.toc-ante{font-size:.95rem;font-weight:700;color:var(--gold);padding:.5rem .5rem;margin-top:.75rem;cursor:pointer;border-radius:4px;transition:background .15s}
.toc-ante:first-child{margin-top:0}
.toc-ante:hover{background:var(--card)}
.toc-blind{font-size:.85rem;color:var(--muted);padding:.3rem .5rem .3rem 1.25rem;cursor:pointer;border-radius:4px;transition:all .15s}
.toc-blind:hover{color:var(--text);background:rgba(255,255,255,.05)}
.toc-ante.active,.toc-blind.active{color:#fff;background:var(--card);font-weight:700}
.toc-blind.active::before{content:'▸ ';color:var(--gold)}
@media(max-width:768px){.detail-layout{flex-direction:column}.toc{display:none}}
.lightbox{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.92);z-index:100;justify-content:center;align-items:center}
.lightbox.active{display:flex}.lightbox img{max-width:95%;max-height:95%;object-fit:contain}
.lightbox .close{position:absolute;top:1rem;right:1.5rem;font-size:2rem;color:#fff;cursor:pointer}
"""


def _header():
    return '<header><div class="container"><h1><a href="/balatro/" style="color:inherit;text-decoration:none">🃏 <span>Balatro</span> Run Viewer</a></h1></div></header>'


def _lightbox_html():
    return """<div class="lightbox" id="lb" onclick="this.classList.remove('active')"><span class="close">&times;</span><img id="lbi" src="" alt=""></div>
<script>function openLb(src){document.getElementById('lbi').src=src;document.getElementById('lb').classList.add('active')}
document.addEventListener('keydown',function(e){if(e.key==='Escape')document.getElementById('lb').classList.remove('active')})</script>"""


def _html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@app.get("/game/{run_code}", response_class=HTMLResponse)
async def page_game_detail(run_code: str):
    """Server-rendered game detail page."""
    row = await db_pool.fetchrow("SELECT id FROM balatro_runs WHERE run_code = $1", run_code)
    if not row:
        raise HTTPException(404, "Run not found")
    run_data = await get_run(row["id"])
    run = run_data["run"]
    jokers = run_data.get("jokers", [])
    screenshots = run_data.get("screenshots", [])
    catalog = _load_joker_catalog()
    catalog_map = {j["name_en"].lower(): j for j in catalog}

    # Fetch strategy info
    strategy = None
    if run.get("strategy_id"):
        strategy = await db_pool.fetchrow("SELECT * FROM balatro_strategies WHERE id = $1", run["strategy_id"])

    rc = run["run_code"]
    is_running = run["status"] == "running"
    dur = f'{round(run["duration_seconds"] / 60)}分钟' if run.get("duration_seconds") else "-"
    cost = f'${float(run["llm_cost_usd"]):.4f}' if run.get("llm_cost_usd") else "-"
    rd = run.get("rule_decisions") or 0
    ld = run.get("llm_decisions") or 0
    td = rd + ld
    ratio = f"{round(rd / td * 100)}%" if td > 0 else "-"
    icon = "🔄" if is_running else ("🏆" if run.get("won") else "💀")
    status_badge = ' <span class="badge running">运行中</span>' if is_running else ""

    h = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{rc} - Balatro Run Viewer</title><style>{_base_css()}</style></head><body>
{_header()}<div class="container">
<a class="back-btn" href="/balatro/">← 返回列表</a>
<div class="detail-header"><h2>{icon} {rc}{status_badge}</h2>
<div style="font-family:monospace;font-size:.9rem;color:var(--muted);margin:.5rem 0">种子: {run.get('seed') or '未知'} | 策略: {f'<a href="/balatro/strategy/{strategy["id"]}" style="color:var(--gold)">{_html_escape(strategy["name"])}</a>' if strategy else '未知'}</div>
<div class="detail-stats">"""

    for v, l in [
        (f"Ante {run.get('final_ante', '?')}", "关卡"),
        (run.get("hands_played", 0), "出牌"),
        (run.get("discards_used", 0), "弃牌"),
        (run.get("purchases", 0), "购买"),
        (ratio, "Rule率"),
        (dur, "耗时"),
        (cost, "LLM成本"),
    ]:
        h += f'<div class="stat"><div class="val">{v}</div><div class="lbl">{l}</div></div>'
    h += "</div></div>"

    # Jokers
    if jokers:
        h += f'<div class="section"><h3>🃏 小丑牌 ({len(jokers)})</h3><div class="joker-grid">'
        for j in jokers:
            cj = catalog_map.get(j["name"].lower(), {})
            img = f'/balatro/joker-images/{cj["image"]}' if cj.get("image") else ""
            h += '<div class="joker-card">'
            if img:
                h += f'<img src="{img}" alt="{_html_escape(j["name"])}">'
            h += f'<div class="joker-info"><div class="name-en">{_html_escape(j["name"])}</div>'
            if cj.get("name_zh"):
                h += f'<div class="name-zh">{_html_escape(cj["name_zh"])}</div>'
            eff = cj.get("effect_zh") or cj.get("effect_en") or ""
            if eff:
                h += f'<div class="effect">{_html_escape(eff)}</div>'
            h += "</div></div>"
        h += "</div></div>"

    # Build TOC data first (need to scan screenshots)
    import re
    toc_items = []  # [(ante, blind, divider_id)]
    seen_keys = set()
    for i, s in enumerate(screenshots):
        cap = s.get("caption") or s.get("event_type") or ""
        ev = s.get("event_type") or ""
        ante_m = re.search(r"第(\d+)关", cap)
        ante_n = int(ante_m.group(1)) if ante_m else 0
        blind = ""
        for kw in ["商店", "小盲", "大盲", "Boss"]:
            if kw in cap:
                blind = kw
                break
        if not blind:
            if "游戏结束" in cap or ev == "game_over":
                blind = "结束"
            elif "开始" in cap or ev == "game_start":
                blind = "开始"
        key = f"a{ante_n}-{blind}"
        if key not in seen_keys and blind:
            seen_keys.add(key)
            toc_items.append((ante_n, blind, f"blind-{i}"))

    # Feed with detail-layout wrapper
    h += '<div class="detail-layout"><div class="detail-main">'
    h += f'<div class="section"><h3>📷 游戏过程 ({len(screenshots)} 张)'
    if is_running:
        h += ' <span class="badge running">实时更新中</span>'
    h += '</h3><div class="feed">'

    last_blind_key = ""
    for i, s in enumerate(screenshots):
        cap = s.get("caption") or s.get("event_type") or ""
        ev = s.get("event_type") or ""
        url = f"/balatro/screenshots/{rc}/screenshots/{s['filename']}"

        # Blind divider
        ante_m = re.search(r"第(\d+)关", cap)
        ante_n = int(ante_m.group(1)) if ante_m else 0
        blind = ""
        for kw in ["商店", "小盲", "大盲", "Boss"]:
            if kw in cap:
                blind = kw
                break
        if not blind:
            if "游戏结束" in cap or ev == "game_over":
                blind = "结束"
            elif "开始" in cap or ev == "game_start":
                blind = "开始"
        key = f"a{ante_n}-{blind}"
        if key != last_blind_key and blind:
            label = f"第{ante_n}关 {blind}" if ante_n > 0 else blind
            h += f'<div class="blind-divider" id="blind-{i}">{label}</div>'
            last_blind_key = key

        # Source tag
        src_tag = ""
        if "[Rule]" in cap:
            src_tag = ' <span class="source-tag rule">RULE</span>'
        elif "[LLM]" in cap:
            src_tag = ' <span class="source-tag llm">LLM</span>'

        h += '<div class="feed-entry">'
        if cap:
            h += f'<div class="caption">{_html_escape(cap)}{src_tag}</div>'

        # Score bar
        est = s.get("estimated_score")
        act = s.get("actual_score")
        if est and act is not None:
            err = s.get("score_error") or 0
            err_pct = round(err * 100)
            err_cls = "good" if abs(err) < 0.2 else ("ok" if abs(err) < 0.5 else "bad")
            sign = "+" if err >= 0 else ""
            h += f'<div class="score-bar"><span class="score-est">估分 {est}</span>'
            h += f'<span class="score-arrow">→</span><span class="score-act">实际 {act}</span>'
            h += f'<span class="score-err {err_cls}">{sign}{err_pct}%</span></div>'

        h += f'<img class="screenshot" src="{url}" alt="" onclick="openLb(this.src)" loading="lazy" onerror="this.style.display=\'none\'">'
        h += "</div>"

    h += "</div></div></div>"  # close feed, section, detail-main

    # TOC sidebar
    h += '<div class="toc"><div class="toc-title">目录</div>'
    last_toc_ante = -1
    for ante_n, blind, div_id in toc_items:
        if ante_n > 0 and ante_n != last_toc_ante:
            last_toc_ante = ante_n
            h += f'<div class="toc-ante" data-target="{div_id}" onclick="document.getElementById(\'{div_id}\').scrollIntoView({{behavior:\'smooth\'}})">第{ante_n}关</div>'
        if blind:
            h += f'<div class="toc-blind" data-target="{div_id}" onclick="document.getElementById(\'{div_id}\').scrollIntoView({{behavior:\'smooth\'}})">{blind}</div>'
    h += "</div></div>"  # close toc, detail-layout

    # Auto-refresh for running games
    if is_running:
        h += '<script>setTimeout(function(){location.reload()},5000)</script>'

    # Scroll spy for TOC
    h += """<script>
(function(){
  var dividers=document.querySelectorAll('.blind-divider[id]');
  var tocEls=document.querySelectorAll('.toc-ante,.toc-blind');
  if(!dividers.length||!tocEls.length)return;
  var obs=new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if(e.isIntersecting){
        var id=e.target.id;
        tocEls.forEach(function(t){
          var match=t.getAttribute('data-target')===id;
          t.classList.toggle('active',match);
          if(match)t.scrollIntoView({block:'nearest',behavior:'smooth'});
        });
      }
    });
  },{rootMargin:'-10% 0px -80% 0px'});
  dividers.forEach(function(d){obs.observe(d)});
})();
</script>"""

    h += f"</div>{_lightbox_html()}</body></html>"
    return HTMLResponse(h)


@app.get("/api/strategies")
async def list_strategies():
    """List all strategies with aggregated stats."""
    rows = await db_pool.fetch(
        """SELECT s.*,
           COUNT(r.id) AS total_runs,
           SUM(CASE WHEN r.won THEN 1 ELSE 0 END) AS total_wins,
           ROUND(AVG(r.final_ante), 1) AS calc_avg_ante,
           ROUND(AVG(r.llm_cost_usd)::numeric, 4) AS avg_cost,
           ROUND(AVG(r.duration_seconds)::numeric, 0) AS avg_duration
           FROM balatro_strategies s
           LEFT JOIN balatro_runs r ON r.strategy_id = s.id
           GROUP BY s.id ORDER BY s.created_at DESC"""
    )
    return [dict(r) for r in rows]


@app.get("/api/strategies/{strategy_id}")
async def get_strategy(strategy_id: int):
    """Get strategy detail with stats."""
    s = await db_pool.fetchrow("SELECT * FROM balatro_strategies WHERE id = $1", strategy_id)
    if not s:
        raise HTTPException(404, "Strategy not found")
    runs = await db_pool.fetch(
        """SELECT id, run_code, status, won, final_ante, seed, hands_played,
           discards_used, duration_seconds, llm_cost_usd, llm_model, played_at
           FROM balatro_runs WHERE strategy_id = $1 ORDER BY played_at DESC""",
        strategy_id
    )
    return {"strategy": dict(s), "runs": [dict(r) for r in runs]}

@app.get("/strategy/{strategy_id}", response_class=HTMLResponse)
async def page_strategy_detail(strategy_id: int):
    """Server-rendered strategy detail page with code, summary, tree."""
    s = await db_pool.fetchrow("SELECT * FROM balatro_strategies WHERE id = $1", strategy_id)
    if not s:
        raise HTTPException(404, "Strategy not found")

    runs = await db_pool.fetch(
        "SELECT * FROM balatro_runs WHERE strategy_id = $1 ORDER BY played_at DESC", strategy_id)
    total = len(runs)
    wins = sum(1 for r in runs if r.get("won"))
    win_rate = f"{round(wins / total * 100)}%" if total > 0 else "-"
    avg_ante = round(sum(r.get("final_ante") or 0 for r in runs) / total, 1) if total > 0 else "-"

    # Strategy tree: ancestors + children
    ancestors = []
    pid = s.get("parent_id")
    while pid:
        anc = await db_pool.fetchrow("SELECT id, name, code_hash, created_at FROM balatro_strategies WHERE id = $1", pid)
        if not anc:
            break
        ancestors.insert(0, anc)
        pid = anc.get("parent_id")
    children = await db_pool.fetch(
        "SELECT id, name, code_hash, created_at FROM balatro_strategies WHERE parent_id = $1 ORDER BY created_at", strategy_id)

    import json as _json
    from datetime import timezone, timedelta
    sgt = timezone(timedelta(hours=8))

    name = s.get("name") or "未命名"
    code_hash = s.get("code_hash") or "-"
    model = s.get("model") or "-"
    params = s.get("params")
    if isinstance(params, str):
        params = _json.loads(params)
    source_code = s.get("source_code") or ""
    summary = s.get("summary") or ""

    h = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>策略 {_html_escape(name)} - Balatro</title><style>{_base_css()}
pre.code{{background:#0d1117;padding:1rem;border-radius:8px;overflow-x:auto;font-size:.8rem;line-height:1.5;max-height:600px;overflow-y:auto;border:1px solid #333}}
.tree{{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin:.75rem 0}}
.tree-node{{padding:.3rem .6rem;border-radius:6px;font-size:.85rem;font-family:monospace}}
.tree-node.current{{background:var(--accent);color:#fff;font-weight:700}}
.tree-node.ancestor{{background:var(--surface);color:var(--muted)}}
.tree-node.child{{background:var(--card);color:var(--gold)}}
.tree-arrow{{color:var(--muted);font-size:.8rem}}
</style></head><body>
{_header()}<div class="container">
<a class="back-btn" href="/balatro/">← 返回列表</a>
<div class="detail-header">
<h2>🧠 {_html_escape(name)}</h2>
<div style="font-family:monospace;font-size:.9rem;color:var(--muted);margin:.5rem 0">
哈希: {code_hash} | 模型: {model}
</div>"""

    # Strategy tree
    if ancestors or children:
        h += '<div class="tree"><span style="color:var(--muted);font-size:.8rem">演进:</span>'
        for a in ancestors:
            atime = a["created_at"].astimezone(sgt).strftime("%m/%d %H:%M") if a.get("created_at") else ""
            h += f'<a href="/balatro/strategy/{a["id"]}" class="tree-node ancestor">{_html_escape(a["name"] or a["code_hash"][:8])}<br><span style="font-size:.7rem">{atime}</span></a><span class="tree-arrow">→</span>'
        cur_time = s["created_at"].astimezone(sgt).strftime("%m/%d %H:%M") if s.get("created_at") else ""
        h += f'<span class="tree-node current">{_html_escape(name)}<br><span style="font-size:.7rem">{cur_time}</span></span>'
        for c in children:
            ctime = c["created_at"].astimezone(sgt).strftime("%m/%d %H:%M") if c.get("created_at") else ""
            h += f'<span class="tree-arrow">→</span><a href="/balatro/strategy/{c["id"]}" class="tree-node child">{_html_escape(c["name"] or c["code_hash"][:8])}<br><span style="font-size:.7rem">{ctime}</span></a>'
        h += '</div>'

    # Stats
    h += '<div class="detail-stats">'
    for v, l in [(total, "总局数"), (wins, "胜场"), (win_rate, "胜率"), (avg_ante, "平均Ante")]:
        h += f'<div class="stat"><div class="val">{v}</div><div class="lbl">{l}</div></div>'
    h += "</div></div>"

    # Summary
    if summary:
        h += f'<div class="section"><h3>📝 策略摘要</h3><div style="background:var(--surface);padding:1rem;border-radius:8px;line-height:1.6">{_html_escape(summary)}</div></div>'

    # Params
    if params:
        h += '<div class="section"><h3>⚙️ 参数</h3><div style="background:var(--surface);padding:1rem;border-radius:8px;font-family:monospace;font-size:.9rem">'
        for k, v in params.items():
            h += f'<div>{k}: <span style="color:var(--gold)">{v}</span></div>'
        h += "</div></div>"

    # Source code
    if source_code:
        h += f'<div class="section"><h3>💻 策略代码</h3><pre class="code"><code>{_html_escape(source_code)}</code></pre></div>'

    # Runs table
    if runs:
        h += f'<div class="section"><h3>🎮 关联运行 ({total} 局)</h3>'
        h += '<table class="run-table"><thead><tr><th>编号</th><th>进度</th><th>种子</th><th>出牌</th><th>弃牌</th><th>耗时</th><th>时间</th></tr></thead><tbody>'
        for r in runs:
            rc = r["run_code"] or str(r["id"])
            if r["status"] == "running":
                prog = '<span class="badge running">运行中</span>'
            elif r.get("won"):
                prog = '<span class="badge win">通关</span>'
            else:
                p = r.get("progress") or f'Ante {r.get("final_ante", "?")}'
                prog = f'<span class="badge loss">{p}</span>'
            seed = (r.get("seed") or "-")[:8]
            dur = f'{round(r["duration_seconds"] / 60)}m' if r.get("duration_seconds") else "-"
            t = r["played_at"].astimezone(sgt).strftime("%m/%d %H:%M") if r.get("played_at") else ""
            h += f'<tr onclick="location.href=\'/balatro/game/{rc}\'" style="cursor:pointer">'
            h += f'<td class="run-code">{rc}</td><td>{prog}</td><td style="font-family:monospace;font-size:.8rem;color:var(--muted)">{seed}</td>'
            h += f'<td>{r.get("hands_played", 0)}</td><td>{r.get("discards_used", 0)}</td><td>{dur}</td><td>{t}</td></tr>'
        h += "</tbody></table></div>"

    h += "</div></body></html>"
    return HTMLResponse(h)




@app.get("/seed/{seed_val}", response_class=HTMLResponse)
async def page_seed_detail(seed_val: str):
    """Server-rendered seed detail page."""
    runs = await db_pool.fetch(
        """SELECT r.*, s.name as strategy_name, s.id as sid
           FROM balatro_runs r LEFT JOIN balatro_strategies s ON r.strategy_id = s.id
           WHERE r.seed = $1 ORDER BY r.played_at DESC""", seed_val)
    if not runs:
        raise HTTPException(404, "Seed not found")

    from datetime import timezone, timedelta
    sgt = timezone(timedelta(hours=8))
    total = len(runs)
    wins = sum(1 for r in runs if r.get("won"))
    best_ante = max(r.get("final_ante") or 0 for r in runs)
    strategies_used = set(r.get("strategy_name") or "?" for r in runs if r.get("sid"))

    h = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>种子 {_html_escape(seed_val)} - Balatro</title><style>{_base_css()}</style></head><body>
{_header()}<div class="container">
<a class="back-btn" href="/balatro/">← 返回列表</a>
<div class="detail-header">
<h2>🌱 种子: <span style="font-family:monospace">{_html_escape(seed_val)}</span></h2>
<div class="detail-stats">"""
    for v, l in [(total, "运行次数"), (wins, "胜场"), (best_ante, "最佳Ante"), (len(strategies_used), "策略数")]:
        h += f'<div class="stat"><div class="val">{v}</div><div class="lbl">{l}</div></div>'
    h += "</div></div>"

    if strategies_used:
        h += '<div class="section"><h3>🧠 使用过的策略</h3><div style="display:flex;gap:.5rem;flex-wrap:wrap">'
        for sn in strategies_used:
            h += f'<span style="background:var(--surface);padding:.3rem .6rem;border-radius:6px;font-size:.85rem">{_html_escape(sn)}</span>'
        h += "</div></div>"

    h += f'<div class="section"><h3>🎮 关联运行 ({total} 局)</h3>'
    h += '<table class="run-table"><thead><tr><th>编号</th><th>进度</th><th>策略</th><th>出牌</th><th>弃牌</th><th>耗时</th><th>时间</th></tr></thead><tbody>'
    for r in runs:
        rc = r["run_code"] or str(r["id"])
        if r["status"] == "running":
            prog = '<span class="badge running">运行中</span>'
        elif r.get("won"):
            prog = '<span class="badge win">通关</span>'
        else:
            p = r.get("progress") or f'Ante {r.get("final_ante", "?")}'
            prog = f'<span class="badge loss">{p}</span>'
        sn = r.get("strategy_name") or "-"
        sid = r.get("sid")
        scell = f'<a href="/balatro/strategy/{sid}" onclick="event.stopPropagation()" style="color:var(--gold);font-size:.8rem">{_html_escape(sn)}</a>' if sid else "-"
        dur = f'{round(r["duration_seconds"] / 60)}m' if r.get("duration_seconds") else "-"
        t = r["played_at"].astimezone(sgt).strftime("%m/%d %H:%M") if r.get("played_at") else ""
        h += f'<tr onclick="location.href=\'/balatro/game/{rc}\'" style="cursor:pointer">'
        h += f'<td class="run-code">{rc}</td><td>{prog}</td><td>{scell}</td>'
        h += f'<td>{r.get("hands_played", 0)}</td><td>{r.get("discards_used", 0)}</td><td>{dur}</td><td>{t}</td></tr>'
    h += "</tbody></table></div></div></body></html>"
    return HTMLResponse(h)


@app.get("/", response_class=HTMLResponse)
async def page_list():
    """Server-rendered run list page with tabs."""
    rows = await db_pool.fetch(
        """SELECT r.*, s.name as strategy_name, s.id as sid,
           (SELECT COUNT(*) FROM balatro_screenshots sc WHERE sc.run_id = r.id) AS screenshot_count
           FROM balatro_runs r LEFT JOIN balatro_strategies s ON r.strategy_id = s.id
           ORDER BY r.played_at DESC NULLS LAST LIMIT 50"""
    )

    # Fetch score error stats per run
    score_stats = await db_pool.fetch(
        """SELECT run_id, COUNT(*) as cnt,
           AVG(ABS(score_error)) as avg_err,
           MAX(ABS(score_error)) as max_err
           FROM balatro_screenshots
           WHERE estimated_score IS NOT NULL AND actual_score IS NOT NULL
           GROUP BY run_id"""
    )
    score_map = {s["run_id"]: s for s in score_stats}

    # Fetch strategies for tab 2
    strategies = await db_pool.fetch(
        """SELECT s.*,
           COUNT(r.id) as run_count,
           ROUND(AVG(r.final_ante)::numeric, 1) as avg_ante,
           SUM(CASE WHEN r.won THEN 1 ELSE 0 END) as wins
           FROM balatro_strategies s
           LEFT JOIN balatro_runs r ON r.strategy_id = s.id
           GROUP BY s.id ORDER BY s.created_at DESC"""
    )

    # Fetch seeds for tab 3
    seeds = await db_pool.fetch(
        """SELECT seed, COUNT(*) as run_count,
           MAX(final_ante) as best_ante,
           ROUND(AVG(final_ante)::numeric, 1) as avg_ante,
           SUM(CASE WHEN won THEN 1 ELSE 0 END) as wins,
           COUNT(DISTINCT strategy_id) as strategy_count,
           MIN(played_at) as first_played
           FROM balatro_runs
           WHERE seed IS NOT NULL AND seed != ''
           GROUP BY seed ORDER BY run_count DESC, best_ante DESC"""
    )

    from datetime import timezone, timedelta
    sgt = timezone(timedelta(hours=8))

    h = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Balatro Run Viewer</title><style>{_base_css()}
.tabs{{display:flex;gap:0;margin-bottom:1.5rem;border-bottom:2px solid #333}}
.tab{{padding:.6rem 1.5rem;cursor:pointer;font-size:1rem;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}}
.tab:hover{{color:var(--text)}}
.tab.active{{color:var(--gold);border-bottom-color:var(--gold)}}
.tab-content{{display:none}}.tab-content.active{{display:block}}
</style></head><body>
{_header()}<div class="container">
<div class="tabs">
<div class="tab active" onclick="switchTab('games')">🎮 运行 ({len(rows)})</div>
<div class="tab" onclick="switchTab('strategies')">🧠 策略 ({len(strategies)})</div>
<div class="tab" onclick="switchTab('seeds')">🌱 种子 ({len(seeds)})</div>
</div>
<div id="tab-games" class="tab-content active">
<table class="run-table"><thead><tr><th>编号</th><th>进度</th><th>策略</th><th>种子</th><th>出牌</th><th>弃牌</th><th>Rule率</th><th>估分误差</th><th>耗时</th><th>成本</th><th>时间</th></tr></thead><tbody>"""

    for r in rows:
        rc = r["run_code"] or str(r["id"])
        if r["status"] == "running":
            progress_cell = '<span class="badge running">运行中</span>'
        elif r.get("won"):
            progress_cell = '<span class="badge win">通关</span>'
        else:
            prog = r.get("progress") or ""
            if prog:
                progress_cell = f'<span class="badge loss">{prog}</span>'
            else:
                progress_cell = f'<span class="badge loss">Ante {r.get("final_ante", "?")}</span>'

        seed = r.get("seed") or "-"
        if len(seed) > 8:
            seed = seed[:8]
        rd = r.get("rule_decisions") or 0
        ld = r.get("llm_decisions") or 0
        td = rd + ld
        ratio = f"{round(rd / td * 100)}%" if td > 0 else "-"
        dur = f'{round(r["duration_seconds"] / 60)}m' if r.get("duration_seconds") else "-"
        cost = f'${float(r["llm_cost_usd"]):.4f}' if r.get("llm_cost_usd") else "-"
        t = r["played_at"].astimezone(sgt).strftime("%m/%d %H:%M") if r.get("played_at") else ""

        ss = score_map.get(r["id"])
        if ss and ss["cnt"] > 0:
            avg_e = float(ss["avg_err"] or 0) * 100
            max_e = float(ss["max_err"] or 0) * 100
            err_cls = "good" if avg_e < 20 else ("ok" if avg_e < 50 else "bad")
            err_cell = f'<span class="score-err {err_cls}">均{avg_e:.0f}% 峰{max_e:.0f}% ({ss["cnt"]}手)</span>'
        else:
            err_cell = "-"

        sname = r.get("strategy_name") or "-"
        sid = r.get("sid")
        strategy_cell = f'<a href="/balatro/strategy/{sid}" onclick="event.stopPropagation()" style="color:var(--gold);font-size:.8rem">{_html_escape(sname)}</a>' if sid else "-"

        h += f'<tr onclick="location.href=\'/balatro/game/{rc}\'" style="cursor:pointer">'
        h += f'<td class="run-code">{rc}</td><td>{progress_cell}</td><td>{strategy_cell}</td><td style="font-family:monospace;font-size:.8rem;color:var(--muted)">{seed}</td>'
        h += f'<td>{r.get("hands_played", 0)}</td><td>{r.get("discards_used", 0)}</td>'
        h += f'<td>{ratio}</td><td>{err_cell}</td><td>{dur}</td><td>{cost}</td><td>{t}</td></tr>'

    h += "</tbody></table></div>"

    # Strategies tab
    h += """<div id="tab-strategies" class="tab-content">
<table class="run-table"><thead><tr><th>策略名</th><th>模型</th><th>哈希</th><th>局数</th><th>胜率</th><th>平均Ante</th><th>演进自</th><th>创建时间</th></tr></thead><tbody>"""

    for st in strategies:
        sname = st.get("name") or "未命名"
        model = (st.get("model") or "-").split("/")[-1]
        chash = (st.get("code_hash") or "-")[:8]
        rc = st.get("run_count") or 0
        wins = st.get("wins") or 0
        wr = f"{round(wins / rc * 100)}%" if rc > 0 else "-"
        aa = st.get("avg_ante") or "-"
        parent = ""
        if st.get("parent_id"):
            parent = f'<a href="/balatro/strategy/{st["parent_id"]}" style="color:var(--muted);font-size:.8rem">← 父策略</a>'
        ct = st["created_at"].astimezone(sgt).strftime("%m/%d %H:%M") if st.get("created_at") else ""
        h += f'<tr onclick="location.href=\'/balatro/strategy/{st["id"]}\'" style="cursor:pointer">'
        h += f'<td class="run-code">{_html_escape(sname)}</td><td>{model}</td><td style="font-family:monospace;font-size:.8rem;color:var(--muted)">{chash}</td>'
        h += f'<td>{rc}</td><td>{wr}</td><td>{aa}</td><td>{parent}</td><td>{ct}</td></tr>'

    h += """</tbody></table></div>
<div id="tab-seeds" class="tab-content">
<table class="run-table"><thead><tr><th>种子</th><th>运行次数</th><th>策略数</th><th>最佳Ante</th><th>平均Ante</th><th>胜率</th><th>首次使用</th></tr></thead><tbody>"""

    for sd in seeds:
        seed_val = sd["seed"] or "-"
        rc = sd["run_count"] or 0
        sc = sd["strategy_count"] or 0
        ba = sd["best_ante"] or "-"
        aa = sd["avg_ante"] or "-"
        wins = sd["wins"] or 0
        wr = f"{round(wins / rc * 100)}%" if rc > 0 else "-"
        fp = sd["first_played"].astimezone(sgt).strftime("%m/%d %H:%M") if sd.get("first_played") else ""
        h += f'<tr onclick="location.href=\'/balatro/seed/{seed_val}\'" style="cursor:pointer">'
        h += f'<td class="run-code" style="font-family:monospace">{seed_val}</td>'
        h += f'<td>{rc}</td><td>{sc}</td><td>{ba}</td><td>{aa}</td><td>{wr}</td><td>{fp}</td></tr>'

    h += """</tbody></table></div>
<script>
var tabs=['games','strategies','seeds'];
function switchTab(name){
  document.querySelectorAll('.tab').forEach(function(t,i){t.classList.toggle('active',tabs[i]===name)});
  tabs.forEach(function(n){document.getElementById('tab-'+n).classList.toggle('active',n===name)});
}
</script>
</div></body></html>"""
    return HTMLResponse(h)
