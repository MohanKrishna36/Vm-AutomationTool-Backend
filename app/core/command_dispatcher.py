import asyncio
import time
import uuid
import paramiko
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable
from datetime import datetime

# Read-only commands whose results can be safely cached for CACHE_TTL seconds.
_READ_ONLY_KEYS = {
    "sys:status", "sys:uptime", "sys:memory", "sys:disk", "sys:cpu",
    "sys:hostname", "sys:env", "pkg:list", "net:ifconfig", "net:ports",
    "net:routes", "file:df", "proc:list", "proc:top", "proc:count",
    "diag:health", "diag:logs", "diag:connectivity",
}
CACHE_TTL = 30  # seconds


class Priority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


PRIORITY_PREFIX = {"!!": Priority.CRITICAL, "!": Priority.HIGH, "~": Priority.LOW}

CATEGORY_META = {
    "sys":  {"label": "System",      "color": "#3b82f6"},
    "pkg":  {"label": "Packages",    "color": "#8b5cf6"},
    "net":  {"label": "Network",     "color": "#06b6d4"},
    "file": {"label": "File System", "color": "#f59e0b"},
    "proc": {"label": "Processes",   "color": "#ef4444"},
    "diag": {"label": "Diagnostics", "color": "#10b981"},
    "raw":  {"label": "Raw Shell",   "color": "#6b7280"},
}


@dataclass
class ParsedCommand:
    raw: str
    category: str
    action: str
    args: list
    priority: Priority
    command_id: str


@dataclass
class CommandResult:
    command_id: str
    raw_command: str
    category: str
    action: str
    output: str
    success: bool
    execution_time_ms: float
    timestamp: str
    error: str = ""
    cached: bool = False

    def to_dict(self):
        return {
            "command_id": self.command_id,
            "raw_command": self.raw_command,
            "category": self.category,
            "action": self.action,
            "output": self.output,
            "success": self.success,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp,
            "error": self.error,
            "cached": self.cached,
        }


class CommandDispatcher:
    def __init__(self):
        self._registry: dict[str, dict] = {}
        self._history: list[CommandResult] = []
        self._cache: dict[str, tuple] = {}   # key -> (CommandResult, monotonic_time)
        self._metrics = {
            "total_dispatched": 0,
            "total_success": 0,
            "total_failed": 0,
            "total_cached": 0,
            "total_execution_time_ms": 0.0,
        }
        self._register_builtins()

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, category: str, action: str, handler: Callable,
                 description: str, default_priority: Priority = Priority.NORMAL):
        key = f"{category}:{action}"
        self._registry[key] = {
            "category": category,
            "action": action,
            "handler": handler,
            "description": description,
            "default_priority": default_priority.name,
            "category_label": CATEGORY_META.get(category, {}).get("label", category),
            "category_color": CATEGORY_META.get(category, {}).get("color", "#6b7280"),
        }

    def _register_builtins(self):
        # SYS
        self.register("sys", "status",   self._sys_status,   "Full system overview",           Priority.NORMAL)
        self.register("sys", "uptime",   self._sys_uptime,   "System uptime",                  Priority.NORMAL)
        self.register("sys", "memory",   self._sys_memory,   "Memory usage (free -h)",         Priority.NORMAL)
        self.register("sys", "disk",     self._sys_disk,     "Disk usage (df -h)",             Priority.NORMAL)
        self.register("sys", "cpu",      self._sys_cpu,      "CPU information",                Priority.NORMAL)
        self.register("sys", "hostname", self._sys_hostname, "System hostname",                Priority.NORMAL)
        self.register("sys", "reboot",   self._sys_reboot,   "Reboot the VM",                  Priority.CRITICAL)
        self.register("sys", "poweroff", self._sys_poweroff, "Power off the VM",               Priority.CRITICAL)
        self.register("sys", "env",      self._sys_env,      "Environment variables",          Priority.LOW)
        # PKG
        self.register("pkg", "install",  self._pkg_install,  "Install package (apk add)",      Priority.NORMAL)
        self.register("pkg", "remove",   self._pkg_remove,   "Remove package (apk del)",       Priority.NORMAL)
        self.register("pkg", "list",     self._pkg_list,     "List installed packages",        Priority.LOW)
        self.register("pkg", "update",   self._pkg_update,   "Update package index",           Priority.NORMAL)
        self.register("pkg", "search",   self._pkg_search,   "Search available packages",      Priority.LOW)
        self.register("pkg", "info",     self._pkg_info,     "Package info (apk info)",        Priority.LOW)
        # NET
        self.register("net", "ping",     self._net_ping,     "Ping a host",                    Priority.NORMAL)
        self.register("net", "ifconfig", self._net_ifconfig, "Network interfaces (ip addr)",   Priority.NORMAL)
        self.register("net", "ports",    self._net_ports,    "Open listening ports (ss)",      Priority.NORMAL)
        self.register("net", "dns",      self._net_dns,      "DNS lookup (nslookup)",          Priority.NORMAL)
        self.register("net", "curl",     self._net_curl,     "HTTP request (curl)",            Priority.NORMAL)
        self.register("net", "routes",   self._net_routes,   "Routing table (ip route)",       Priority.NORMAL)
        # FILE
        self.register("file", "ls",      self._file_ls,      "List directory (ls -lah)",       Priority.NORMAL)
        self.register("file", "cat",     self._file_cat,     "Print file contents",            Priority.NORMAL)
        self.register("file", "mkdir",   self._file_mkdir,   "Create directory",               Priority.NORMAL)
        self.register("file", "find",    self._file_find,    "Find files by pattern",          Priority.LOW)
        self.register("file", "df",      self._file_df,      "Disk free space",                Priority.NORMAL)
        self.register("file", "tail",    self._file_tail,    "Tail end of a file",             Priority.NORMAL)
        self.register("file", "wc",      self._file_wc,      "Word/line/byte count",           Priority.NORMAL)
        # PROC
        self.register("proc", "list",    self._proc_list,    "List running processes (ps)",    Priority.NORMAL)
        self.register("proc", "kill",    self._proc_kill,    "Kill a process by PID",          Priority.HIGH)
        self.register("proc", "top",     self._proc_top,     "Top processes snapshot",         Priority.NORMAL)
        self.register("proc", "count",   self._proc_count,   "Count running processes",        Priority.LOW)
        # DIAG
        self.register("diag", "health",      self._diag_health,      "Full health report",     Priority.HIGH)
        self.register("diag", "benchmark",   self._diag_benchmark,   "CPU/disk benchmark",     Priority.LOW)
        self.register("diag", "logs",        self._diag_logs,        "Recent system logs",     Priority.NORMAL)
        self.register("diag", "connectivity",self._diag_connectivity,"Test internet access",   Priority.NORMAL)

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse(self, raw: str) -> ParsedCommand:
        raw = raw.strip()
        priority = Priority.NORMAL

        for prefix, prio in PRIORITY_PREFIX.items():
            if raw.startswith(prefix + " "):
                priority = prio
                raw = raw[len(prefix):].strip()
                break

        parts = raw.split()
        if not parts:
            return ParsedCommand(raw="", category="raw", action="", args=[],
                                 priority=priority, command_id=uuid.uuid4().hex[:8])

        cmd_part = parts[0]
        args = parts[1:]

        if ":" in cmd_part:
            category, action = cmd_part.split(":", 1)
        else:
            category, action = "raw", raw
            args = []

        return ParsedCommand(
            raw=raw,
            category=category.lower(),
            action=action.lower(),
            args=args,
            priority=priority,
            command_id=uuid.uuid4().hex[:8],
        )

    # ── Execution ─────────────────────────────────────────────────────────────

    async def execute(self, parsed: ParsedCommand, ssh: paramiko.SSHClient,
                      cwd: str = None) -> CommandResult:
        key = f"{parsed.category}:{parsed.action}"

        # ── Cache check (read-only commands only) ─────────────────────────────
        if key in _READ_ONLY_KEYS:
            entry = self._cache.get(key)
            if entry:
                cached_result, cached_at = entry
                if time.monotonic() - cached_at < CACHE_TTL:
                    hit = CommandResult(
                        command_id=parsed.command_id,
                        raw_command=parsed.raw,
                        category=parsed.category,
                        action=parsed.action,
                        output=cached_result.output,
                        success=cached_result.success,
                        execution_time_ms=0.0,
                        timestamp=datetime.utcnow().isoformat() + "Z",
                        error=cached_result.error,
                        cached=True,
                    )
                    self._metrics["total_dispatched"] += 1
                    self._metrics["total_cached"] += 1
                    self._metrics["total_success"] += 1
                    self._history.append(hit)
                    if len(self._history) > 500:
                        self._history = self._history[-500:]
                    return hit

        # ── Execute ───────────────────────────────────────────────────────────
        start = time.monotonic()
        output = error = ""

        try:
            if key in self._registry:
                output = await self._registry[key]["handler"](parsed.args, ssh)
                success = True
            elif parsed.category == "raw":
                # Prepend cwd so raw shell commands run in the right directory
                cmd = parsed.action
                if cwd and cwd not in ("~", "/root", "/"):
                    cmd = f"cd {cwd} 2>/dev/null && {cmd}"
                output, err = await self._ssh(cmd, ssh)
                error = err
                success = not err or bool(output)
            else:
                error = f"Unknown command '{key}'. Run GET /dispatcher/registry for available commands."
                success = False
        except Exception as exc:
            error = str(exc)
            success = False

        elapsed = round((time.monotonic() - start) * 1000, 2)
        result = CommandResult(
            command_id=parsed.command_id,
            raw_command=parsed.raw,
            category=parsed.category,
            action=parsed.action,
            output=output,
            success=success,
            execution_time_ms=elapsed,
            timestamp=datetime.utcnow().isoformat() + "Z",
            error=error,
            cached=False,
        )

        self._metrics["total_dispatched"] += 1
        self._metrics["total_execution_time_ms"] += elapsed
        if success:
            self._metrics["total_success"] += 1
        else:
            self._metrics["total_failed"] += 1

        # Store in cache if read-only and successful
        if key in _READ_ONLY_KEYS and success:
            self._cache[key] = (result, time.monotonic())

        self._history.append(result)
        if len(self._history) > 500:
            self._history = self._history[-500:]

        return result

    async def batch_execute(self, commands: list[str], ssh: paramiko.SSHClient) -> list[CommandResult]:
        parsed_list = [self.parse(c) for c in commands]
        parsed_list.sort(key=lambda p: p.priority)
        results = []
        for p in parsed_list:
            results.append(await self.execute(p, ssh))
        return results

    # ── Public queries ────────────────────────────────────────────────────────

    def get_registry(self) -> list[dict]:
        out = []
        for k, v in self._registry.items():
            out.append({
                "key": k,
                "category": v["category"],
                "action": v["action"],
                "description": v["description"],
                "default_priority": v["default_priority"],
                "category_label": v["category_label"],
                "category_color": v["category_color"],
            })
        return out

    def get_registry_grouped(self) -> dict:
        groups: dict[str, list] = {}
        for item in self.get_registry():
            cat = item["category"]
            groups.setdefault(cat, []).append(item)
        return groups

    def get_history(self, limit: int = 100) -> list[dict]:
        return [r.to_dict() for r in self._history[-limit:]]

    def get_metrics(self) -> dict:
        total = self._metrics["total_dispatched"]
        executed = total - self._metrics["total_cached"]
        return {
            **self._metrics,
            "avg_execution_time_ms": round(
                self._metrics["total_execution_time_ms"] / executed if executed else 0, 2),
            "success_rate": round(
                self._metrics["total_success"] / total * 100 if total else 0, 1),
            "cache_hit_rate": round(
                self._metrics["total_cached"] / total * 100 if total else 0, 1),
            "registered_commands": len(self._registry),
            "history_size": len(self._history),
            "cache_size": len(self._cache),
        }

    # ── SSH helper ────────────────────────────────────────────────────────────

    async def _ssh(self, cmd: str, ssh: paramiko.SSHClient, timeout: int = 30) -> tuple[str, str]:
        loop = asyncio.get_event_loop()

        def _run():
            _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
            out = stdout.read().decode(errors="replace").strip()
            err = stderr.read().decode(errors="replace").strip()
            return out, err

        return await loop.run_in_executor(None, _run)

    # ── SYS handlers ─────────────────────────────────────────────────────────

    async def _sys_status(self, args, ssh):
        cmd = (
            "echo '=== HOSTNAME ===' && hostname && "
            "echo '=== UPTIME ===' && uptime && "
            "echo '=== MEMORY ===' && free -h && "
            "echo '=== DISK ===' && df -h / && "
            "echo '=== LOAD ===' && cat /proc/loadavg && "
            "echo '=== OS ===' && cat /etc/os-release 2>/dev/null | head -4"
        )
        out, err = await self._ssh(cmd, ssh)
        return out or err

    async def _sys_uptime(self, args, ssh):
        out, err = await self._ssh("uptime -p 2>/dev/null; uptime", ssh)
        return out or err

    async def _sys_memory(self, args, ssh):
        out, err = await self._ssh("free -h && echo '' && cat /proc/meminfo | head -8", ssh)
        return out or err

    async def _sys_disk(self, args, ssh):
        out, err = await self._ssh("df -h && echo '' && du -sh /* 2>/dev/null | sort -rh | head -8", ssh)
        return out or err

    async def _sys_cpu(self, args, ssh):
        out, err = await self._ssh(
            "grep -E 'model name|cpu cores|cpu MHz' /proc/cpuinfo | head -6 && echo '' && nproc", ssh)
        return out or err

    async def _sys_hostname(self, args, ssh):
        out, err = await self._ssh("hostname -f 2>/dev/null || hostname", ssh)
        return out or err

    async def _sys_reboot(self, args, ssh):
        await self._ssh("reboot", ssh)
        return "Reboot command sent to VM."

    async def _sys_poweroff(self, args, ssh):
        await self._ssh("poweroff", ssh)
        return "Power-off command sent to VM."

    async def _sys_env(self, args, ssh):
        out, err = await self._ssh("env | sort", ssh)
        return out or err

    # ── PKG handlers ─────────────────────────────────────────────────────────

    async def _pkg_install(self, args, ssh):
        if not args:
            return "Usage: pkg:install <package>"
        out, err = await self._ssh(f"apk add --no-cache {' '.join(args)} 2>&1", ssh, timeout=120)
        return out or err

    async def _pkg_remove(self, args, ssh):
        if not args:
            return "Usage: pkg:remove <package>"
        out, err = await self._ssh(f"apk del {' '.join(args)} 2>&1", ssh)
        return out or err

    async def _pkg_list(self, args, ssh):
        out, err = await self._ssh("apk list --installed 2>/dev/null | head -40", ssh)
        return out or err

    async def _pkg_update(self, args, ssh):
        out, err = await self._ssh("apk update 2>&1", ssh)
        return out or err

    async def _pkg_search(self, args, ssh):
        if not args:
            return "Usage: pkg:search <query>"
        out, err = await self._ssh(f"apk search {args[0]} 2>/dev/null | head -25", ssh)
        return out or err

    async def _pkg_info(self, args, ssh):
        if not args:
            return "Usage: pkg:info <package>"
        out, err = await self._ssh(f"apk info {args[0]} 2>&1", ssh)
        return out or err

    # ── NET handlers ─────────────────────────────────────────────────────────

    async def _net_ping(self, args, ssh):
        host = args[0] if args else "8.8.8.8"
        count = args[1] if len(args) > 1 else "4"
        out, err = await self._ssh(f"ping -c {count} {host} 2>&1", ssh, timeout=20)
        return out or err

    async def _net_ifconfig(self, args, ssh):
        out, err = await self._ssh("ip addr show 2>/dev/null || ifconfig 2>/dev/null", ssh)
        return out or err

    async def _net_ports(self, args, ssh):
        out, err = await self._ssh("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null | head -20", ssh)
        return out or err

    async def _net_dns(self, args, ssh):
        domain = args[0] if args else "google.com"
        out, err = await self._ssh(
            f"nslookup {domain} 2>/dev/null || host {domain} 2>/dev/null", ssh)
        return out or err

    async def _net_curl(self, args, ssh):
        if not args:
            return "Usage: net:curl <url>"
        out, err = await self._ssh(
            f"curl -s -o /dev/null -w 'HTTP %{{http_code}} | Time: %{{time_total}}s | Size: %{{size_download}} bytes' {args[0]} 2>&1",
            ssh, timeout=15)
        return out or err

    async def _net_routes(self, args, ssh):
        out, err = await self._ssh("ip route show 2>/dev/null || route -n 2>/dev/null", ssh)
        return out or err

    # ── FILE handlers ────────────────────────────────────────────────────────

    async def _file_ls(self, args, ssh):
        path = args[0] if args else "."
        out, err = await self._ssh(f"ls -lah {path} 2>&1", ssh)
        return out or err

    async def _file_cat(self, args, ssh):
        if not args:
            return "Usage: file:cat <path>"
        out, err = await self._ssh(f"cat {args[0]} 2>&1 | head -100", ssh)
        return out or err

    async def _file_mkdir(self, args, ssh):
        if not args:
            return "Usage: file:mkdir <path>"
        out, err = await self._ssh(f"mkdir -p {args[0]} && echo 'Created: {args[0]}'", ssh)
        return out or err

    async def _file_find(self, args, ssh):
        pattern = args[0] if args else "*"
        path = args[1] if len(args) > 1 else "."
        out, err = await self._ssh(f"find {path} -name '{pattern}' 2>/dev/null | head -25", ssh)
        return out or err

    async def _file_df(self, args, ssh):
        out, err = await self._ssh("df -h 2>&1", ssh)
        return out or err

    async def _file_tail(self, args, ssh):
        if not args:
            return "Usage: file:tail <path> [lines]"
        lines = args[1] if len(args) > 1 else "20"
        out, err = await self._ssh(f"tail -n {lines} {args[0]} 2>&1", ssh)
        return out or err

    async def _file_wc(self, args, ssh):
        if not args:
            return "Usage: file:wc <path>"
        out, err = await self._ssh(f"wc {args[0]} 2>&1", ssh)
        return out or err

    # ── PROC handlers ────────────────────────────────────────────────────────

    async def _proc_list(self, args, ssh):
        out, err = await self._ssh("ps aux 2>/dev/null | head -25 || ps 2>/dev/null", ssh)
        return out or err

    async def _proc_kill(self, args, ssh):
        if not args:
            return "Usage: proc:kill <pid>"
        out, err = await self._ssh(f"kill {args[0]} && echo 'Signal sent to PID {args[0]}'", ssh)
        return out or err

    async def _proc_top(self, args, ssh):
        out, err = await self._ssh("top -bn1 2>/dev/null | head -25", ssh)
        return out or err

    async def _proc_count(self, args, ssh):
        out, err = await self._ssh("ps aux 2>/dev/null | wc -l | xargs -I{} echo '{} running processes'", ssh)
        return out or err

    # ── DIAG handlers ────────────────────────────────────────────────────────

    async def _diag_health(self, args, ssh):
        cmd = (
            "echo '====== HEALTH REPORT ======' && "
            "echo '[UPTIME]' && uptime && "
            "echo '[MEMORY]' && free -h && "
            "echo '[DISK]' && df -h / && "
            "echo '[LOAD]' && cat /proc/loadavg && "
            "echo '[PROCESSES]' && ps aux 2>/dev/null | wc -l | xargs echo 'Total:' && "
            "echo '[NETWORK]' && ip addr show 2>/dev/null | grep 'inet ' | head -3 && "
            "echo '[OS]' && cat /etc/os-release 2>/dev/null | grep PRETTY_NAME"
        )
        out, err = await self._ssh(cmd, ssh)
        return out or err

    async def _diag_benchmark(self, args, ssh):
        cmd = (
            "echo '====== BENCHMARK ======' && "
            "echo '[CPU: calculate primes]' && time (factor $(seq 2 5000) > /dev/null) 2>&1 && "
            "echo '[DISK: write 50MB]' && time dd if=/dev/zero of=/tmp/_bench bs=1M count=50 2>&1 && "
            "rm -f /tmp/_bench && "
            "echo '[MEMORY: free]' && free -h"
        )
        out, err = await self._ssh(cmd, ssh, timeout=60)
        return out or err

    async def _diag_logs(self, args, ssh):
        out, err = await self._ssh(
            "dmesg 2>/dev/null | tail -20 || journalctl -n 20 2>/dev/null || cat /var/log/syslog 2>/dev/null | tail -20",
            ssh)
        return out or err

    async def _diag_connectivity(self, args, ssh):
        cmd = (
            "echo '[DNS]' && nslookup google.com 2>/dev/null | head -4 && "
            "echo '[PING 8.8.8.8]' && ping -c 2 8.8.8.8 2>&1 && "
            "echo '[HTTPS]' && curl -s -o /dev/null -w 'google.com: HTTP %{http_code}' https://google.com 2>/dev/null"
        )
        out, err = await self._ssh(cmd, ssh, timeout=15)
        return out or err


# Module-level singleton shared across all requests
dispatcher = CommandDispatcher()
