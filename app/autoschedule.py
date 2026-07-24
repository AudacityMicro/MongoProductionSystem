from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Iterable


@dataclass(frozen=True)
class ScheduleJob:
    pallet_id: str
    name: str
    program: str
    tools: frozenset[int]
    original_position: int


def _tool_labels(tools: Iterable[int]) -> list[str]:
    return [f"T{number}" for number in sorted(tools)]


def _next_use(tool: int, jobs: tuple[ScheduleJob, ...], start: int) -> int:
    for index in range(start, len(jobs)):
        if tool in jobs[index].tools:
            return index
    return len(jobs) + 1


def simulate_tool_plan(
    jobs: tuple[ScheduleJob, ...],
    initial_tools: frozenset[int],
    capacity: int,
) -> dict:
    loaded = set(initial_tools)
    steps: list[dict] = []
    total_loads = 0
    total_unloads = 0

    for index, job in enumerate(jobs):
        if len(job.tools) > capacity:
            raise ValueError(f"{job.name} requires {len(job.tools)} tools, exceeding ATC capacity {capacity}.")
        missing = set(job.tools) - loaded
        remove_count = max(0, len(loaded) + len(missing) - capacity)
        removable = loaded - set(job.tools)
        # Keep tools needed soon and evict tools whose next use is furthest away.
        removals = sorted(
            removable,
            key=lambda tool: (-_next_use(tool, jobs, index + 1), tool),
        )[:remove_count]
        loaded.difference_update(removals)
        loaded.update(missing)
        total_loads += len(missing)
        total_unloads += len(removals)
        steps.append(
            {
                "pallet_id": job.pallet_id,
                "name": job.name,
                "program": job.program,
                "required_tools": _tool_labels(job.tools),
                "load_before": _tool_labels(missing),
                "unload_before": _tool_labels(removals),
                "atc_after": _tool_labels(loaded),
            }
        )

    return {
        "pallet_ids": [job.pallet_id for job in jobs],
        "loads": total_loads,
        "unloads": total_unloads,
        "tool_movements": total_loads + total_unloads,
        "steps": steps,
    }


def _plan_key(plan: dict, jobs: tuple[ScheduleJob, ...]) -> tuple:
    positions = {job.pallet_id: job.original_position for job in jobs}
    return (
        plan["tool_movements"],
        plan["loads"],
        tuple(positions[pallet_id] for pallet_id in plan["pallet_ids"]),
    )


def _greedy_orders(
    jobs: tuple[ScheduleJob, ...],
    initial_tools: frozenset[int],
    capacity: int,
) -> list[tuple[ScheduleJob, ...]]:
    candidates: list[tuple[ScheduleJob, ...]] = [jobs]
    for seed in jobs:
        order = [seed]
        remaining = [job for job in jobs if job != seed]
        while remaining:
            best_job = min(
                remaining,
                key=lambda job: _plan_key(
                    simulate_tool_plan(tuple(order + [job]), initial_tools, capacity),
                    jobs,
                ),
            )
            order.append(best_job)
            remaining.remove(best_job)
        candidates.append(tuple(order))
    return candidates


def optimize_tool_schedule(
    jobs: tuple[ScheduleJob, ...],
    initial_tools: frozenset[int],
    capacity: int,
) -> tuple[dict, str]:
    if len(jobs) <= 1:
        return simulate_tool_plan(jobs, initial_tools, capacity), "No optimization needed"

    if len(jobs) <= 8:
        orders = permutations(jobs)
        method = "Exact minimum search"
    else:
        orders = _greedy_orders(jobs, initial_tools, capacity)
        method = "Greedy search with local improvement"

    best_order: tuple[ScheduleJob, ...] | None = None
    best_plan: dict | None = None
    for order_value in orders:
        order = tuple(order_value)
        plan = simulate_tool_plan(order, initial_tools, capacity)
        if best_plan is None or _plan_key(plan, jobs) < _plan_key(best_plan, jobs):
            best_order, best_plan = order, plan

    if len(jobs) > 8 and best_order is not None and best_plan is not None:
        # Swapping any pair catches useful improvements missed by nearest-tool greedy ordering.
        improved = True
        passes = 0
        while improved and passes < 5:
            improved = False
            passes += 1
            for left in range(len(best_order) - 1):
                for right in range(left + 1, len(best_order)):
                    candidate = list(best_order)
                    candidate[left], candidate[right] = candidate[right], candidate[left]
                    candidate_order = tuple(candidate)
                    candidate_plan = simulate_tool_plan(candidate_order, initial_tools, capacity)
                    if _plan_key(candidate_plan, jobs) < _plan_key(best_plan, jobs):
                        best_order, best_plan = candidate_order, candidate_plan
                        improved = True

    assert best_plan is not None
    return best_plan, method
