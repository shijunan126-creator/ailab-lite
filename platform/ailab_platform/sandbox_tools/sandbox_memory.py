"""Measure retained process memory without charging reclaimable file mappings."""

from pathlib import Path


def retained_bytes(process: Path) -> int:
    total = 0
    values = {}
    with (process / "smaps").open(encoding="ascii") as stream:
        for line in stream:
            key = line.partition(":")[0]
            if key in {"Anonymous", "Private_Dirty", "Shared_Dirty", "Swap"}:
                values[key] = int(line.split()[1]) * 1024
            elif key == "VmFlags":
                total += max(values.get("Anonymous", 0), values.get("Private_Dirty", 0) + values.get("Shared_Dirty", 0))
                total += values.get("Swap", 0)
                values = {}
    if values:
        raise RuntimeError("invalid_memory_accounting")
    return total
