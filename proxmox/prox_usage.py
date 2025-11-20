#!/usr/bin/env python3

import os
import re
import sys
from typing import List, Tuple

import subprocess

# ANSI color codes
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

def get_pve_status() -> str:
    # Get uptime
    with open("/proc/uptime") as f:
        uptime_seconds = float(f.readline().split()[0])
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)

    # Get memory info
    mem_info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, val = line.split(":", 1)
            mem_info[key] = int(val.strip().split()[0])  # in KB

    total_mem = mem_info.get("MemTotal", 0) // 1024
    free_mem = mem_info.get("MemFree", 0) // 1024
    buff_cache = mem_info.get("Buffers", 0) // 1024 + mem_info.get("Cached", 0) // 1024
    used_mem = total_mem - free_mem - buff_cache

    # Get CPU usage using top one-liner
    try:
        top_out = subprocess.check_output(["top", "-bn1"], text=True)
        cpu_line = next(line for line in top_out.splitlines() if line.startswith("%Cpu(s)"))
    except Exception:
        cpu_line = "CPU info not available"

    # Get CPU model and core count
    cpu_model = "Unknown"
    cpu_cores = 0
    with open("/proc/cpuinfo") as f:
        for line in f:
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
            elif line.startswith("processor"):
                cpu_cores += 1

    status = (
        f"{BOLD}{CYAN}### PVE Status{RESET}\n"
        f"{YELLOW}- Uptime:{RESET} {days} days, {hours}:{minutes:02d}\n"
        f"{YELLOW}- Memory:{RESET} {GREEN}{used_mem} MB used{RESET} / {total_mem} MB total "
        f"(buff/cache: {buff_cache} MB)\n"
        f"{YELLOW}- CPU:{RESET} {CYAN}{cpu_model}{RESET} ({cpu_cores} cores)\n"
        f"{YELLOW}- Usage:{RESET} {RED}{cpu_line}{RESET}\n"
    )
    return status



# --- Type Aliases ---
MarkdownRow = str

# --- Argument Parsing ---
def parse_args(args: List[str]) -> str:
    outfile: str = ""
    i: int = 0
    while i < len(args):
        if args[i] in ("-o", "--outfile") and i + 1 < len(args):
            outfile = args[i + 1]
            i += 2
        else:
            print("Usage: script.py [-o|--outfile output.md]")
            sys.exit(1)
    return outfile

# --- VM Parsing ---
def parse_vm_configs() -> Tuple[List[MarkdownRow], int, int, int]:
    vm_rows: List[MarkdownRow] = []
    total_vm_mem: int = 0
    total_vm_disk: int = 0
    total_vm_vcpus: int = 0

    for cfg_path in glob_files("/etc/pve/qemu-server", ".conf"):
        vmid: str = os.path.basename(cfg_path).replace(".conf", "")
        name: str = extract_value(cfg_path, "^name") or "(no name)"
        mem: int = int(extract_value(cfg_path, "^memory") or "0")
        cores: int = int(extract_value(cfg_path, "^cores") or "1")
        total_vm_vcpus += cores

        disk_size_sum: int = 0
        disk_locations: List[str] = []
        with open(cfg_path) as f:
            for line in f:
                disk_match = re.match(r"^(scsi|sata|virtio|efidisk)\d+:\s*([^\s,]+)", line)
                if disk_match:
                    storage_location = disk_match.group(2).split(":")[0].split(",")[0]
                    disk_locations.append(storage_location)
                    size_match = re.search(r",size=([\d]+)([GM])", line)
                    if size_match:
                        size_val: int = int(size_match.group(1))
                        unit: str = size_match.group(2)
                        if unit == "G":
                            disk_size_sum += size_val
                        elif unit == "M":
                            disk_size_sum += size_val // 1024

        disk_locations_str = ", ".join(disk_locations) if disk_locations else "-"
        vm_rows.append(f"| {vmid:<6} | {name:<18} | {mem:<12} | {cores:<5} | {disk_size_sum:<9} | {disk_locations_str:<20} |")
        total_vm_mem += mem
        total_vm_disk += disk_size_sum

    return vm_rows, total_vm_mem, total_vm_disk, total_vm_vcpus

# --- LXC Parsing ---
def parse_lxc_configs() -> Tuple[List[dict], int, int, int]:
    lxc_data: List[dict] = []
    total_lxc_mem: int = 0
    total_lxc_disk: int = 0
    total_lxc_cores: int = 0

    for cfg_path in glob_files("/etc/pve/lxc", ".conf"):
        vmid: str = os.path.basename(cfg_path).replace(".conf", "")
        name: str = extract_value(cfg_path, "^hostname") or "(no name)"
        mem: int = int(extract_value(cfg_path, "^memory") or "0")
        cores: int = int(extract_value(cfg_path, "^cores") or "1")
        swap: int = int(extract_value(cfg_path, "^swap") or "0")
        total_lxc_cores += cores

        rootfs_line: str = extract_line(cfg_path, "^rootfs")
        disk_gb: int = 0
        disk_location: str = "-"
        if rootfs_line:
            rootfs_match = re.match(r"^rootfs:\s*([^\s,]+)", rootfs_line)
            if rootfs_match:
                disk_location = rootfs_match.group(1).split(":")[0].split(",")[0]
            size_match = re.search(r",size=([\d]+)([GM])", rootfs_line)
            if size_match:
                size_val: int = int(size_match.group(1))
                unit: str = size_match.group(2)
                if unit == "G":
                    disk_gb = size_val
                elif unit == "M":
                    disk_gb = size_val // 1024

        lxc_data.append({
            'id': vmid,
            'hostname': name,
            'memory': mem,
            'cores': cores,
            'swap': swap,
            'disk': disk_gb,
            'location': disk_location
        })
        total_lxc_mem += mem
        total_lxc_disk += disk_gb

    return lxc_data, total_lxc_mem, total_lxc_disk, total_lxc_cores

# --- Helpers ---
def glob_files(directory: str, extension: str) -> List[str]:
    return [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(extension)]

def extract_value(filepath: str, pattern: str) -> str:
    with open(filepath) as f:
        for line in f:
            if re.match(pattern, line, re.IGNORECASE):
                parts = re.split(r"[=:]", line, maxsplit=1)
                if len(parts) == 2:
                    return parts[1].strip()
    return ""

def extract_line(filepath: str, pattern: str) -> str:
    with open(filepath) as f:
        for line in f:
            if re.match(pattern, line, re.IGNORECASE):
                return line.strip()
    return ""

# --- Output ---
def print_output(
    vm_rows: List[str],
    lxc_data: List[dict],
    total_vm_mem: int,
    total_vm_disk: int,
    total_vm_vcpus: int,
    total_lxc_mem: int,
    total_lxc_disk: int,
    total_lxc_cores: int,
    host_cpu_cores: int
) -> None:
    print("======================================")
    print(get_pve_status())
    print("======================================")

    total_alloc_mem = total_vm_mem + total_lxc_mem
    total_alloc_disk = total_vm_disk + total_lxc_disk
    total_alloc_cpu = total_vm_vcpus + total_lxc_cores
    cpu_percentage = (total_alloc_cpu / host_cpu_cores) * 100 if host_cpu_cores else 0

    # ✅ Build LXC header and rows first
    header = f"{BOLD}{YELLOW}| {'LXC ID':<7} | {'Hostname':<18} | {'Memory (MB)':<12} | {'Cores':<5} | {'Swap (MB)':<9} | {'Disk (GB)':<9} | {'Disk Location':<13} |{RESET}"
    separator = "|---------|--------------------|-------------|-------|-----------|-----------|---------------|"

    lxc_rows_formatted = []
    for lxc in lxc_data:
        lxc_rows_formatted.append(
            f"| {lxc['id']:<7} | {lxc['hostname']:<18} | {lxc['memory']:<12} | {lxc['cores']:<5} | {lxc['swap']:<9} | {lxc['disk']:<9} | {lxc['location']:<13} |"
        )

    # ✅ Now build output_lines
    output_lines: List[str] = [
        f"{BOLD}{CYAN}### VM Memory, CPU & Disk Allocation{RESET}",
        f"{BOLD}{YELLOW}| {'VM ID':<6} | {'Name':<18} | {'Memory (MB)':<12} | {'vCPUs':<5} | {'Disk (GB)':<9} | {'Disk Location(s)':<20} |{RESET}",
        "|--------|--------------------|-------------|-------|-----------|----------------------|",
        *vm_rows,
        "",
        f"{BOLD}{CYAN}### LXC Memory, CPU & Disk Allocation{RESET}",
        header,
        separator,
        *lxc_rows_formatted,
        "",
        f"{BOLD}{CYAN}### CPU Summary{RESET}",
        f"- Total VM vCPUs: **{total_vm_vcpus}**",
        f"- Total LXC CPU cores: **{total_lxc_cores}**",
        f"- Total Allocated CPU: **{total_alloc_cpu}**",
        f"- Host CPU Capacity: **{host_cpu_cores} cores**",
        f"- CPU Allocation: **{cpu_percentage:.2f}%**",
        "",
        f"{BOLD}{CYAN}### Memory & Disk Summary{RESET}",
        f"- Total VM Memory: {total_vm_mem} MB",
        f"- Total VM Disk: {total_vm_disk} GB",
        f"- Total LXC Memory: {total_lxc_mem} MB",
        f"- Total LXC Disk: {total_lxc_disk} GB",
        f"- Total Allocated Memory: {total_alloc_mem} MB",
        f"- Total Allocated Disk: {total_alloc_disk} GB"
    ]

    for line in output_lines:
        print(line)

# --- Main ---
def main() -> None:
    outfile: str = parse_args(sys.argv[1:])
    host_cpu_cores: int = os.cpu_count() or 1

    vm_rows, total_vm_mem, total_vm_disk, total_vm_vcpus = parse_vm_configs()
    lxc_data, total_lxc_mem, total_lxc_disk, total_lxc_cores = parse_lxc_configs()

    if outfile:
        with open(outfile, "w") as f:
            sys.stdout = f
            print_output(vm_rows, lxc_data, total_vm_mem, total_vm_disk, total_vm_vcpus,
                         total_lxc_mem, total_lxc_disk, total_lxc_cores, host_cpu_cores)
        print(f"Saved report to {outfile}")
    else:
        print_output(vm_rows, lxc_data, total_vm_mem, total_vm_disk, total_vm_vcpus,
                     total_lxc_mem, total_lxc_disk, total_lxc_cores, host_cpu_cores)

if __name__ == "__main__":
    main()


