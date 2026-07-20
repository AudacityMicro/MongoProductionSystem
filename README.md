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
- configurable simulated/physical robot mode, controller file access, and robot-originated supervisor transport;
- transactional APIs with optimistic revision conflict detection;
- an automated API and persistence test suite.

Authentication and program-header parsing remain future work. Physical robot
and mill actions must be commissioned with the cell empty before Run Mode is used.

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
the Debugging page uses only controller data and never falls back to simulated
values. The preferred transport is the persistent robot-originated supervisor;
RTDE remains the explicitly bounded legacy path while the supervisor is disabled.

### Robot supervisor commissioning

The generated `mongo_supervisor.script` opens one outbound connection from
Mongo to the application computer. It provides sequenced movement events,
digital I/O, joint state, TCP state, and deterministic recovery without
continuously reconnecting to several controller services. The wire protocol
uses checksummed, network-byte-order 32-bit integers so it works on the installed
PolyScope 3.2 controller without newer URScript string-conversion functions.

1. Give the application computer a stable DHCP reservation or static address.
2. Confirm Mongo resolves the Settings hostname, default
   `DESKTOP-KF5I73N.lan`, to that address.
3. Allow inbound TCP port `50010` through Windows Firewall on the private
   network. The backend listens on `0.0.0.0:50010` by default.
4. Keep **Use persistent supervisor** disabled. Save the endpoint, heartbeat,
   telemetry, reconnect, robot SFTP, and motion settings.
5. Press **Rebuild generated scripts**. This atomically updates the local and
   robot copies, including `mongo_supervisor.script`.
6. Press **Run no-motion bootstrap**. This starts no movement. Require a live
   robot session, a recent heartbeat, and matching robot/backend sequences.
7. Enable **Use persistent supervisor** and save only after that test passes.
8. Commission in this order: telemetry soak, manual output test, empty-fork
   pool move, mill load/unload, backend restart, then a controlled network loss.

The detected controller is PolyScope `3.2.20175`. If URControl accepts the
script transfer but never starts the outbound connection, install the final
vendor-approved 3.2 maintenance update before retrying; do not bypass the
no-motion handshake gate.

Every supervisor command is committed to SQLite before transmission. A command
that was not attempted may use the configured legacy fallback. Once any send is
attempted, a missing or uncertain result latches the workflow for operator
reconciliation and is never automatically repeated.

### Diagnostics and support bundles

The Debugging page includes a persistent diagnostic timeline and a **Download
support bundle** action. It captures backend starts/stops, validated supervisor
handshakes, disconnect and protocol faults, network-test results, failed API
requests, mutating requests, and requests slower than one second. Supervisor
command and robot-motion ledgers are included in the JSON bundle.

Diagnostics are written to rotating files under `data/`, retained across
backend restarts, and excluded from Git. Passwords, tokens, and credentials are
redacted. Generate a bundle immediately after a fault before changing settings
or manually reconciling physical state.

The **Queue reliability test** freezes the current queue when it starts. For
each queued pallet physically in the Pool, it picks from the current slot,
visits only the outer mill pre-entry staging pose, and returns the pallet to the
same slot. It never opens the mill door, changes the Erowa output, enters the
mill, or runs PathPilot. **Stop after current pallet** cancels only between
complete cycles. Rebuild generated robot scripts after upgrading before using
this test with the physical robot.

### Run mode

Run mode processes queued pallets in queue order. Before it starts physical
motion, it verifies that the generated robot scripts match Settings, that the
local and PathPilot copies of `mongo_mill_load_position.nc` match the saved G53
coordinates, and that every queued machining program exists on PathPilot.

For each pallet, the controller sequence is:

1. Run `mongo_mill_load_position.nc` on PathPilot and wait for Idle.
2. Run the pool-pick script followed by `load_mill.script` on Mongo.
3. Run the pallet's assigned PathPilot program and wait for Idle.
4. If `RESULTS.TXT` changed during the cycle, archive it with the program name
   and a UTC timestamp. Missing or unchanged results produce an operator alert
   but do not stop the remaining pallet workflow.
5. Run `mongo_mill_load_position.nc` again and wait for Idle.
6. Run `unload_mill.script` followed by the original pool-position put script.
7. Mark the pallet complete, return it to its pool position, and advance.

The RESULTS.TXT source and archive directory are configured under Mill Programs
in Settings. Both paths must remain inside the configured PathPilot program
directory; the archive directory is created automatically.

When action confirmation is enabled, the operator must approve loading,
machining, and unloading. Stopping run mode prevents the next workflow step; it
does not abort a controller program that has already been dispatched. Use the
machine or robot safety controls when an immediate stop is required.

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

## Remaining production work

Controller commands still need authenticated operator roles, a durable audit
trail, and configured door/Erowa confirmation inputs before unattended use.
