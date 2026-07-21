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
- program discovery plus automatic Fusion-posted tool and cycle-time metadata;
- configurable numbered pool positions and weight display units;
- configurable simulated/physical robot mode, controller file access, and robot-originated supervisor transport;
- transactional APIs with optimistic revision conflict detection;
- an automated API and persistence test suite.

Authentication remains future work. Physical robot
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

When a pallet is Robot-held or in the Mill, its intended return position remains
reserved and appears as a translucent ghost in the Pool. **Put away pallet**
returns it to that position automatically. If the reservation is unavailable,
the scheduler chooses the nearest numbered empty position that is not reserved
for another pallet; operators can still choose a destination explicitly.

Use the Settings page to choose the server-side program source folder, allowed
file extensions, display weight unit, and Pool position count. Program scans
are recursive. If a previously assigned program disappears, refreshing the
program list clears that assignment and reports the affected pallet.

### Program tool and cycle metadata

`Tormach_Inspection.cps` writes a small versioned metadata block near the top
of every newly posted program:

```text
(MPS-METADATA-V1)
(MPS-TOOLS:1,20,105)
(MPS-CYCLE-SECONDS:91.2)
(MPS-CYCLE-BASIS:FUSION-CUTTING-ESTIMATE)
```

When a program is assigned to a pallet, the application reads only the first
64 KiB from the configured PathPilot `gcode` directory and stores its tools and
estimated cycle time with the pallet. **Refresh programs** rescans that same
PathPilot directory and updates metadata for all existing assignments. If the
controller cannot be reached, refresh fails without clearing any assignments.

Existing NC files must be reposted with the updated post processor before they
contain this header. Fusion's section cycle-time estimate covers cutting moves
and excludes rapid traversal, so the displayed time is an estimate rather than
measured elapsed machine time.

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
   and a UTC timestamp. An unchanged file is skipped normally; missing results
   or archive failures alert the operator without stopping the pallet workflow.
5. Run `mongo_mill_load_position.nc` again and wait for Idle.
6. Run `unload_mill.script` followed by the original pool-position put script.
7. Mark the pallet complete, return it to its pool position, and advance.

PathPilot program monitoring does not treat Idle alone as success. An E-stop,
disabled control, LinuxCNC execution/interpreter error, or alarm/error message
pauses the queue before the pallet is moved or marked complete. The operator is
prompted to clear and inspect the mill and either retry that same program or
stop Run Mode with the pallet left in its current physical position.

The RESULTS.TXT source and archive directory are configured under Mill Programs
in Settings. Both paths must remain inside the configured PathPilot program
directory; the archive directory is created automatically.

When action confirmation is enabled, the operator must approve loading,
machining, and unloading. Stopping Run Mode prevents the next workflow step;
it does not abort a controller program that has already been dispatched. Use
the machine or robot safety controls when an immediate stop is required.

### Long-running production

The scheduler is designed to monitor machining cycles for up to 30 days. A
successful PathPilot start is recorded before monitoring begins. If SSH or
telemetry drops while that program is already running, the scheduler does not
retry the G-code, unload the pallet, or send another controller action. It
backs off read-only telemetry attempts from one second up to 30 seconds and
continues monitoring when the controller reconnects. The active run status and
diagnostic timeline record the outage and recovery.

Before Run Mode starts, and before an individual robot transfer has been
dispatched, transient robot or PathPilot telemetry failures are retried
automatically with bounded backoff. Each retry confirms that Run Mode remains
enabled before it does anything. An E-stop, machine alarm, invalid setup, robot
safety fault, motion timeout, or any missing response after a controller
command may have been sent remains latched for operator reconciliation; these
are intentionally not retried because doing so could duplicate physical work.

Run the backend from a Windows account that remains logged in, with the PC set
not to sleep or hibernate. Keep the database, `runtime/`, and `data/` folders
on a local SSD rather than a mapped/network drive. SQLite runs in WAL mode with
a 30-second writer wait; do not sync or back up the live database file by
copying it while the app is running. Use a SQLite-aware backup or stop the app
cleanly first.

The application deliberately does not resume motion automatically after a
backend restart, power failure, or uncertain robot command. It records an
interrupted state so an operator can reconcile the physical cell before
continuing. This prevents a recovered process from duplicating an in-progress
move.

For unattended backend availability, `run_backend_watchdog.ps1` checks the
local health endpoint every 30 seconds and calls the existing safe launcher
only when the backend is not reachable. The launcher refuses to replace a
reachable backend that has an active or uncertain production workflow. To run
the watchdog after the next user logon, open an elevated PowerShell in the
project folder and run:

```powershell
.\install_backend_watchdog.ps1
```

This preserves the fail-safe rule: a crash can restore the web service, but it
cannot silently resume robot or mill motion. Configure Windows power settings
so the host does not sleep, and use a UPS for the host, network gear, robot,
and mill controller where practical.

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
