# Mongo Production System

Mongo Production System will schedule pallets and coordinate work between the
Mongo robot and the mill. This repository is a clean production foundation
created after validating controller communication in a separate proof of
concept.

## Current state

The application currently contains:

- a FastAPI application;
- a persistent SQLite pallet database managed by Alembic migrations;
- a graphical Pool, Queue, Machine, and Storage scheduling board;
- pallet creation, editing, duplication, deletion, and program assignment;
- automatic unique pallet names selected from a curated artist catalog;
- program discovery from a configurable server-side source folder;
- configurable numbered pool positions and weight display units;
- configurable simulated/physical robot mode and RTDE connection settings;
- transactional APIs with optimistic revision conflict detection;
- an automated API and persistence test suite.

Robot control, authentication, program execution, program-header parsing, and
automatic machine-state synchronization are intentionally not implemented yet.

## Run locally

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m app
```

Open `http://localhost:8000`. Other machines on the same network can use the
host computer's IPv4 address and port `8000`, provided Windows Firewall allows
inbound TCP traffic on that port.

Run tests with:

```powershell
pytest
```

## Configuration

Copy `.env.example` to `.env` when local overrides are needed. Environment
variables use the `MPS_` prefix.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MPS_HOST` | `0.0.0.0` | Network interface used by the web server |
| `MPS_PORT` | `8000` | Dashboard port |
| `MPS_LOG_LEVEL` | `info` | Server log level |
| `MPS_DATABASE_URL` | Project-root `mongo-production.db` | Optional SQLAlchemy database URL override |

## Operator workflow

Use the Schedule page to create pallets and move them physically between
numbered Pool positions, the single Machine position, and general Storage.
Queue membership is virtual: a queued pallet remains visible in its physical
Pool position until it moves into the Machine. Moving a pallet into Machine or
Storage removes it from the Queue. Cards can be dragged with a mouse or operated
with explicit controls for touch and keyboard use.

Use the Settings page to choose the server-side program source folder, allowed
file extensions, display weight unit, and Pool position count. Program scans
are recursive. If a previously assigned program disappears, refreshing the
program list clears that assignment and reports the affected pallet.

Settings can also enable a fixed debug simulator on the Schedule page and
choose between a simulated robot and a physical robot connection. In simulated
mode, the Debugging page allows manual digital I/O toggles. In physical mode,
the Debugging page uses only live RTDE data from the configured controller and
never falls back to simulated values.

## Lessons retained from the proof of concept

- Robot commands and robot state must be separate concerns. A successful
  command response does not prove that a program stayed running.
- Very short robot programs can complete before the next status poll. Completion
  must not be reported as a start failure.
- Universal Robots Dashboard Server, RTDE, and SSH/SFTP serve different roles
  and should be isolated behind explicit adapters.
- Controller addresses, ports, credentials, program names, and I/O labels must
  be configuration, never UI constants or committed secrets.
- RTDE data can be temporarily unavailable. State reads need timestamps,
  connection status, and a clear stale-data policy.
- Program transfer must be constrained to an approved controller directory and
  must validate paths before upload, rename, download, or deletion.
- Operator actions need audit records, authorization, idempotency where
  possible, and safe failure behavior before production use.
- Development relaunch behavior does not belong in the production web API.

## Planned architecture

The system will be built in vertical slices:

1. Define pallets, parts, operations, machines, and scheduling states.
2. Add persistent storage and migrations.
3. Build the operator scheduling board.
4. Add read-only machine and robot status adapters.
5. Add audited command execution with safety interlocks.
6. Add program lifecycle management and production reporting.

The next implementation step should define the pallet workflow and scheduling
state model before reconnecting controller commands.
