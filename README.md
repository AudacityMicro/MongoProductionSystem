# Mongo Production System

Mongo Production System will schedule pallets and coordinate work between the
Mongo robot and the mill. This repository is a clean production foundation
created after validating controller communication in a separate proof of
concept.

## Current state

The application currently contains only:

- a FastAPI application;
- a health and version endpoint;
- a blank responsive UI shell;
- environment-based server configuration;
- a small automated test suite.

Robot control, pallet scheduling, persistence, authentication, and production
workflows are intentionally not implemented yet.

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

