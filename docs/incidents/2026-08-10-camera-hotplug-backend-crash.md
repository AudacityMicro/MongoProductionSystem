# 2026-08-10 camera hot-plug backend crash

## Summary

The MPS backend exited while USB cameras were being unplugged, connected, and discovered. The exit happened during an active Run Mode unload transition. Python did not log an exception or orderly shutdown; `runtime/server.stderr.log` ended after a sustained sequence of native OpenCV DirectShow camera-open failures. This makes a native camera/DirectShow failure the leading hypothesis, but not yet a proven root cause.

This incident must be treated as both a camera-isolation problem and a production-command reconciliation problem. Camera hardware changes must never be able to terminate the production coordinator.

## Timeline and durable controller evidence

- `2026-08-10T20:22:44.010101Z`: robot motion `18cf6a35-86c0-4b94-9e51-0faa48c6a55a` was created to unload pallet **Air** from the mill and return it to Pool 3.
- `2026-08-10T20:22:44.016053Z`: robot supervisor command sequence **450** (`unload_mill`) was recorded as sent.
- `2026-08-10T20:22:44.775992Z`: the robot-originated status document reported sequence 450 as `running`.
- `2026-08-10T20:22:59.896467Z`: the last pre-crash status document still reported sequence 450 as running.
- Around `2026-08-10T20:23Z`: the backend stopped responding. There was no Python traceback or clean application shutdown in the server log.
- On restart, MPS correctly disabled Run Mode, marked it `interrupted`, and marked the active robot motion faulted for physical reconciliation instead of resending it.
- After the robot supervisor reconnected, the controller reported sequence **451** `completed` and `latched=false`. This cleared the controller-side latch, but it does not by itself prove Air's physical location.

## State captured after recovery

Captured `2026-08-10T16:35:39-04:00`:

- Backend health: `ok`
- Backend process: 9960, started `2026-08-10T20:31:49.755243Z`
- Database: `ok`
- Run Mode: disabled, state `interrupted`
- Run Mode pallet: Air, reserved return Pool 3
- Active robot motion: none
- Robot supervisor: connected
- Robot last sequence/event: 451 / `completed`
- Robot latch: clear
- Camera API snapshot: no active cameras
- Air's physical location: **not confirmed in software**

Do not infer that Air is in the mill or Pool 3 from the controller completion record alone. Recovery must ask for direct physical confirmation.

## Related but separate Dashboard error

A temporary backend started from a restricted diagnostic process inherited an outbound-network restriction. That process could accept the robot-originated supervisor connection but could not open the robot Dashboard socket, producing WinError 10013. This was an execution-context problem in the temporary replacement backend, not evidence of a new robot safety fault. Normal Windows startup must launch production services.

## Leading failure hypothesis

Evidence supporting a native camera failure:

- The backend exited during repeated camera disconnect/reconnect and discovery activity.
- The final log consists of repeated OpenCV `VIDEOIO(DSHOW)` failures opening camera indices.
- There is no Python exception, FastAPI shutdown sequence, or ordinary process error.
- DirectShow/OpenCV capture currently runs inside the same Python process as the production coordinator, so a native access violation can terminate the entire backend.

Evidence still needed before calling this proven:

- Windows Error Reporting/application event for the exited Python process.
- Process exit code or crash dump identifying OpenCV, DirectShow, or a camera driver DLL.
- A controlled hot-plug reproduction while no production command is active.

## Required hardening work

1. **Isolate camera capture from production control.** Run camera discovery and each capture worker in a supervised child process. A native camera crash must only restart the camera process.
2. **Serialize discovery and capture.** Stop/release camera workers before probing, prevent simultaneous index probes, debounce hardware changes, and reapply configuration only after discovery completes.
3. **Bound camera retries.** Replace two-second unlimited open loops with exponential backoff and a quiet offline state. Avoid repeatedly probing every unused DirectShow index.
4. **Use stable Windows identities.** Map configured cameras by hardware/device identity where possible instead of relying only on OpenCV indices, which can change after hot-plugging.
5. **Evaluate capture backends.** Prefer the most stable Windows backend for each device and use DirectShow only as a controlled fallback. Never switch backends repeatedly inside an active worker.
6. **Make backend supervision verifiable.** Ensure the watchdog is installed, starts outside restricted sessions, writes a durable heartbeat/restart log, and alerts when it cannot recover the backend.
7. **Make relaunch single-flight.** Use a cross-process restart lock so repeated restart requests cannot leave multiple launch helpers waiting on or replacing the same backend.
8. **Retain command uncertainty across crashes.** Continue the existing behavior: never resend a command that was recorded as sent; reconcile controller sequence plus direct physical observation before changing pallet location.
9. **Capture native failures.** Record process exit code and configure Windows Error Reporting/local crash dumps for the backend Python executable, with bounded retention.
10. **Add regression tests.** Cover camera unplug/replug, two different UVC devices, discovery while capture is active, backend death after supervisor dispatch, watchdog recovery, and startup under the normal Windows account.

## Acceptance criteria

- Unplugging, reconnecting, or replacing any camera cannot terminate the backend.
- Camera failure changes only camera status and does not disable or fault an already-safe production workflow.
- No camera discovery operation can block API health responses for more than a short bounded interval.
- A dead camera worker recovers automatically when its device returns.
- A dead backend is restarted by the normal Windows watchdog without inheriting restricted diagnostic permissions.
- Any robot or mill command sent before a backend failure remains explicitly uncertain until controller evidence and required physical checks agree.
