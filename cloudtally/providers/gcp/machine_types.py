"""Parse GCE machine type names into (family, vCPU, RAM GiB)."""
from __future__ import annotations

from dataclasses import dataclass

# GiB of RAM per vCPU for predefined shapes, by (family, kind)
_RAM_PER_VCPU: dict[tuple[str, str], float] = {
    ("e2", "standard"): 4.0,
    ("e2", "highmem"): 8.0,
    ("e2", "highcpu"): 1.0,
    ("n1", "standard"): 3.75,
    ("n1", "highmem"): 6.5,
    ("n1", "highcpu"): 0.9,
    ("n2", "standard"): 4.0,
    ("n2", "highmem"): 8.0,
    ("n2", "highcpu"): 1.0,
    ("n2d", "standard"): 4.0,
    ("n2d", "highmem"): 8.0,
    ("n2d", "highcpu"): 1.0,
    ("c2", "standard"): 4.0,
    ("c2d", "standard"): 4.0,
    ("c2d", "highmem"): 8.0,
    ("c2d", "highcpu"): 2.0,
    ("t2d", "standard"): 4.0,
    ("t2a", "standard"): 4.0,
    ("c4a", "standard"): 4.0,
    ("c4a", "highmem"): 8.0,
    ("c4a", "highcpu"): 2.0,
}

# flat-priced per instance-hour
SHARED_CORE = {"e2-micro", "e2-small", "e2-medium", "f1-micro", "g1-small"}


@dataclass
class MachineShape:
    family: str
    vcpu: float
    ram_gib: float
    flat_key: str | None = None


def _custom_shape(family: str, vcpu: str, ram_mib: str) -> MachineShape | None:
    """None if a field isn't numeric: e2-custom-medium-4096 names a size, not a vCPU count."""
    try:
        return MachineShape(family, float(vcpu), float(ram_mib) / 1024.0)
    except ValueError:
        return None


def parse_machine_type(machine_type: str) -> MachineShape | None:
    """Parse names like e2-standard-4, n2-custom-4-20480, custom-2-8192,
    e2-micro. Returns None for unrecognized shapes."""
    mt = machine_type.strip().lower()
    if not mt:
        return None

    if mt in SHARED_CORE:
        return MachineShape(family=mt.split("-")[0], vcpu=0, ram_gib=0, flat_key=f"{mt}.flat")

    parts = mt.split("-")

    # legacy N1 custom: custom-<vcpu>-<ramMiB>
    if parts[0] == "custom" and len(parts) == 3:
        return _custom_shape("n1", parts[1], parts[2])

    # <family>-custom-<vcpu>-<ramMiB>[-ext]
    if len(parts) >= 4 and parts[1] == "custom":
        return _custom_shape(parts[0], parts[2], parts[3])

    # <family>-<kind>-<vcpu>, optionally with a -lssd or -metal suffix (e.g.
    # c4a-standard-4-lssd, c4a-highmem-96-metal). The suffix doesn't change the CPU/RAM rate,
    # it just attaches local SSD, which we don't price separately here.
    if len(parts) in (3, 4):
        family, kind, n = parts[0], parts[1], parts[2]
        ram_per_vcpu = _RAM_PER_VCPU.get((family, kind))
        if ram_per_vcpu is None or not n.isdigit():
            return None
        vcpu = float(n)
        return MachineShape(family, vcpu, vcpu * ram_per_vcpu)

    return None
