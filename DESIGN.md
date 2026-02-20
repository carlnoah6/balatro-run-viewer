# Balatro Run Viewer - Design Document

> Created: 2026-02-19
> Status: Draft

## 1. Overview

A web app to record, browse, and share Balatro runs with screenshots.
Carl plays Balatro and wants to review past runs, see joker builds, and share highlights.

### Components
- **Database**: Neon PostgreSQL (existing cluster, new tables with `balatro_` prefix)
- **Web**: Static site served via Nginx on Luna server
- **Screenshots**: Local storage + Nginx static serving (same server)
- **Access**: Tailscale Funnel for external access

## 2. Database Schema

### 2.1 `balatro_runs` — Core run data

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| seed | VARCHAR(20) | Game seed (if known) |
| deck | VARCHAR(50) | Deck used (Red, Blue, Checkered, etc.) |
| stake | VARCHAR(20) | Difficulty (White through Gold) |
| final_ante | INTEGER | Last ante reached |
| final_score | BIGINT | Highest score achieved in a single hand |
| won | BOOLEAN | Did the run win (beat Ante 8)? |
| endless_ante | INTEGER | If continued to endless, highest ante |
| joker_count | INTEGER | Number of jokers at end |
| notes | TEXT | Free-form notes about the run |
| played_at | TIMESTAMPTZ | When the run was played |
| created_at | TIMESTAMPTZ | Record creation time |

### 2.2 `balatro_jokers` — Joker lineup per run

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| run_id | INTEGER FK | References balatro_runs |
| name | VARCHAR(100) | Joker name (e.g. "Blueprint", "Baron") |
| position | INTEGER | Slot position (1-5, left to right — order matters!) |
| edition | VARCHAR(20) | NULL, foil, holographic, polychrome |
| eternal | BOOLEAN | Has Eternal sticker |
| perishable | BOOLEAN | Has Perishable sticker |
| rental | BOOLEAN | Has Rental sticker |

### 2.3 `balatro_rounds` — Round-by-round results

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| run_id | INTEGER FK | References balatro_runs |
| ante | INTEGER | Ante number (1-8+) |
| blind_type | VARCHAR(10) | small, big, boss |
| boss_name | VARCHAR(50) | Boss Blind name (NULL for small/big) |
| target_score | BIGINT | Score needed to beat this blind |
| best_hand_score | BIGINT | Best single hand score this round |
| hands_played | INTEGER | Hands used |
| discards_used | INTEGER | Discards used |
| skipped | BOOLEAN | Was this blind skipped? |
| money_after | INTEGER | Money after completing this blind |

### 2.4 `balatro_screenshots` — Screenshot gallery

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| run_id | INTEGER FK | References balatro_runs |
| round_id | INTEGER FK | Optional, references balatro_rounds |
| filename | VARCHAR(255) | Stored filename on disk |
| original_name | VARCHAR(255) | Original upload filename |
| caption | TEXT | Description of the screenshot |
| file_size | INTEGER | Size in bytes |
| width | INTEGER | Image width |
| height | INTEGER | Image height |
| created_at | TIMESTAMPTZ | Upload time |

### 2.5 `balatro_tags` — Tags collected from skipping blinds

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| run_id | INTEGER FK | References balatro_runs |
| ante | INTEGER | Which ante |
| name | VARCHAR(50) | Tag name |

### 2.6 `balatro_strategies` — Strategy definitions (added 2026-02-20)

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| code | VARCHAR(10) UNIQUE | Auto-generated: STR-001, STR-002, ... |
| name | VARCHAR(100) | Strategy name (e.g. "Pairs Build") |
| build_type | VARCHAR(30) | pairs, flush, straight, high_card, etc. |
| description | TEXT | Detailed strategy description |
| key_jokers | JSONB | Key jokers for this strategy |
| priority_hands | JSONB | Target poker hands |
| shop_rules | TEXT | Shop decision guidelines |
| seed_notes | TEXT | Notes on seed-specific behavior |
| tags | JSONB | Tags like ["aggressive", "economy"] |
| win_rate | NUMERIC(5,2) | Cached win rate % |
| avg_ante | NUMERIC(4,1) | Cached avg final ante |
| run_count | INTEGER | Cached run count |
| notes | TEXT | Free-form notes |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | Auto-updated on change |

`balatro_runs.strategy_id` — FK to `balatro_strategies(id)`, nullable (ON DELETE SET NULL)

## 3. Screenshot Storage

- Path: `/home/ubuntu/balatro-screenshots/`
- Naming: `{run_id}/{uuid}.{ext}`
- Served via Nginx at `/balatro/screenshots/`
- Max size: 10MB per image
- Formats: PNG, JPG, WebP

## 4. Web App (Step 2)

- Static HTML/JS/CSS served by Nginx
- API endpoints via a small Python/FastAPI backend
- Path: `/balatro/` on the existing Nginx

## 5. External Access (Step 4)

- Tailscale Funnel on existing `anz-luna.grolar-wage.ts.net`
- Route: `/balatro/` → local Nginx → static files + API
