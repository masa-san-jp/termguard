#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Sequence


ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"


def state_root() -> Path:
    base = os.environ.get("TERMGUARD_HOME")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".termguard"


def sessions_dir() -> Path:
    return state_root() / "sessions"


def now_ts() -> float:
    return time.time()


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def stable_hostname() -> str:
    return os.uname().nodename


def safe_tty() -> str:
    try:
        return os.ttyname(sys.stdin.fileno())
    except Exception:
        return ""


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def normalize_resource(resource: str) -> str:
    raw = resource.strip()
    if not raw:
        raise ValueError("resource must not be empty")
    if ":" in raw:
        kind, value = raw.split(":", 1)
        kind = kind.strip().lower()
        value = value.strip()
        if kind in {"cwd", "file", "path", "port", "branch", "build", "job"}:
            if kind in {"cwd", "file", "path"}:
                return f"{kind}:{str(Path(value).expanduser().resolve(strict=False))}"
            return f"{kind}:{value}"
    return f"raw:{raw}"


def default_resources(cwd: Path) -> list[str]:
    return [f"cwd:{str(cwd.resolve(strict=False))}"]


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class Session:
    id: str
    name: str
    pid: int
    ppid: int
    tty: str
    hostname: str
    cwd: str
    command: list[str]
    resources: list[str]
    started_at: float
    updated_at: float

    @property
    def file(self) -> Path:
        return sessions_dir() / f"{self.id}.json"

    @property
    def command_string(self) -> str:
        return " ".join(shell_quote(part) for part in self.command)

    @property
    def cwd_path(self) -> Path:
        return Path(self.cwd)

    @classmethod
    def from_file(cls, path: Path) -> "Session | None":
        try:
            payload = json.loads(path.read_text())
            return cls(
                id=payload["id"],
                name=payload["name"],
                pid=int(payload["pid"]),
                ppid=int(payload["ppid"]),
                tty=payload.get("tty", ""),
                hostname=payload.get("hostname", ""),
                cwd=payload["cwd"],
                command=list(payload.get("command", [])),
                resources=list(payload.get("resources", [])),
                started_at=float(payload["started_at"]),
                updated_at=float(payload["updated_at"]),
            )
        except Exception:
            return None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)


def ensure_layout() -> None:
    sessions_dir().mkdir(parents=True, exist_ok=True)


def write_session(session: Session) -> None:
    ensure_layout()
    session.updated_at = now_ts()
    session.file.write_text(session.to_json())


def remove_session(session_id: str) -> None:
    try:
        (sessions_dir() / f"{session_id}.json").unlink()
    except FileNotFoundError:
        pass


def load_sessions() -> list[Session]:
    ensure_layout()
    sessions: list[Session] = []
    for path in sorted(sessions_dir().glob("*.json")):
        session = Session.from_file(path)
        if session is None:
            continue
        if not is_pid_alive(session.pid):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        sessions.append(session)
    return sessions


def collect_command(argv: Sequence[str]) -> list[str]:
    return list(argv)


def detect_conflicts(current: Session, others: Iterable[Session]) -> list[tuple[Session, list[str]]]:
    current_resources = set(current.resources)
    conflicts: list[tuple[Session, list[str]]] = []
    for other in others:
        if other.id == current.id:
            continue
        overlap = sorted(current_resources.intersection(other.resources))
        if overlap:
            conflicts.append((other, overlap))
    return conflicts


def format_resources(resources: Sequence[str]) -> str:
    return ", ".join(resources) if resources else "-"


def banner(title: str, body: list[str], color: str = ANSI_RED) -> str:
    lines = [f"{color}{ANSI_BOLD}{title}{ANSI_RESET}"]
    for line in body:
        lines.append(f"{color}{line}{ANSI_RESET}")
    return "\n".join(lines)


def notify(title: str, message: str) -> None:
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    try:
        subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("\a", end="", file=sys.stderr)


def print_status(sessions: list[Session], current_id: str | None = None) -> None:
    if not sessions:
        print(f"{ANSI_GREEN}{ANSI_BOLD}TERMGUARD{ANSI_RESET} no active sessions")
        return
    print(f"{ANSI_CYAN}{ANSI_BOLD}TERMGUARD active sessions{ANSI_RESET}")
    for session in sessions:
        marker = "*" if session.id == current_id else " "
        print(
            f"{marker} {session.id[:8]} "
            f"{session.name} "
            f"pid={session.pid} "
            f"cwd={session.cwd} "
            f"resources={format_resources(session.resources)}"
        )


def build_session(name: str, resources: Sequence[str], command: Sequence[str]) -> Session:
    cwd = Path.cwd().resolve(strict=False)
    normalized = [normalize_resource(resource) for resource in resources]
    if not normalized:
        normalized = default_resources(cwd)
    return Session(
        id=str(uuid.uuid4()),
        name=name or cwd.name or "terminal",
        pid=os.getpid(),
        ppid=os.getppid(),
        tty=safe_tty(),
        hostname=stable_hostname(),
        cwd=str(cwd),
        command=list(command),
        resources=normalized,
        started_at=now_ts(),
        updated_at=now_ts(),
    )


def heartbeat(session: Session) -> None:
    write_session(session)


def update_and_check(session: Session) -> list[tuple[Session, list[str]]]:
    write_session(session)
    sessions = load_sessions()
    return detect_conflicts(session, sessions)


def run_watch(session: Session, interval: float) -> int:
    last_signature: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    try:
        while True:
            conflicts = update_and_check(session)
            signature = tuple((other.id, tuple(overlap)) for other, overlap in conflicts)
            if signature != last_signature:
                last_signature = signature
                if conflicts:
                    other_lines = [
                        f"{other.name} ({other.id[:8]}) pid={other.pid} overlap={format_resources(overlap)}"
                        for other, overlap in conflicts
                    ]
                    msg = banner(
                        f"CONFLICT detected for {session.name}",
                        [
                            f"session={session.id[:8]} resources={format_resources(session.resources)}",
                            *other_lines,
                        ],
                    )
                    print(msg, file=sys.stderr)
                    notify(
                        f"TERMGUARD conflict: {session.name}",
                        "; ".join(other_lines) or "shared resource detected",
                    )
                else:
                    print(
                        f"{ANSI_GREEN}{ANSI_BOLD}TERMGUARD{ANSI_RESET} "
                        f"{session.name} is clear: {format_resources(session.resources)}",
                        file=sys.stderr,
                    )
            time.sleep(interval)
    except KeyboardInterrupt:
        return 130
    finally:
        remove_session(session.id)


def command_run(args: argparse.Namespace) -> int:
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise SystemExit("missing command after --")
    session = build_session(args.name, args.resource or [], cmd)
    write_session(session)
    print(
        f"{ANSI_CYAN}{ANSI_BOLD}TERMGUARD{ANSI_RESET} started "
        f"{session.name} ({session.id[:8]}) resources={format_resources(session.resources)}"
    )
    conflicts = detect_conflicts(session, load_sessions())
    if conflicts:
        lines = [
            f"{other.name} ({other.id[:8]}) pid={other.pid} overlap={format_resources(overlap)}"
            for other, overlap in conflicts
        ]
        print(banner(f"CONFLICT detected for {session.name}", lines), file=sys.stderr)
        notify(f"TERMGUARD conflict: {session.name}", "; ".join(lines))
    proc = subprocess.Popen(session.command)
    last_signature: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    try:
        while True:
            code = proc.poll()
            conflicts = update_and_check(session)
            signature = tuple((other.id, tuple(overlap)) for other, overlap in conflicts)
            if signature != last_signature:
                last_signature = signature
                if conflicts:
                    lines = [
                        f"{other.name} ({other.id[:8]}) pid={other.pid} overlap={format_resources(overlap)}"
                        for other, overlap in conflicts
                    ]
                    print(banner(f"CONFLICT detected for {session.name}", lines), file=sys.stderr)
                    notify(f"TERMGUARD conflict: {session.name}", "; ".join(lines))
            if code is not None:
                return code
            time.sleep(args.interval)
    except KeyboardInterrupt:
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()
    finally:
        remove_session(session.id)


def command_watch(args: argparse.Namespace) -> int:
    session = build_session(args.name, args.resource or [], [])
    write_session(session)
    print(
        f"{ANSI_CYAN}{ANSI_BOLD}TERMGUARD{ANSI_RESET} watching "
        f"{session.name} ({session.id[:8]}) resources={format_resources(session.resources)}"
    )
    return run_watch(session, args.interval)


def command_status(_: argparse.Namespace) -> int:
    sessions = load_sessions()
    print_status(sessions)
    if not sessions:
        return 0
    print()
    for session in sessions:
        conflicts = detect_conflicts(session, sessions)
        if not conflicts:
            continue
        print(f"{ANSI_YELLOW}{session.name}{ANSI_RESET}")
        for other, overlap in conflicts:
            print(f"  -> {other.name} ({other.id[:8]}) overlap={format_resources(overlap)}")
    return 0


def command_stop(args: argparse.Namespace) -> int:
    if args.session_id:
        remove_session(args.session_id)
    elif args.all:
        for session in load_sessions():
            remove_session(session.id)
    else:
        raise SystemExit("use --session-id or --all")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="termguard",
        description="Lightweight conflict alerts for parallel terminal work on macOS.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    run_parser = sub.add_parser("run", help="run a command with conflict monitoring")
    run_parser.add_argument("--name", default="", help="session label")
    run_parser.add_argument("--resource", action="append", default=[], help="resource token, e.g. cwd:., file:src/app.py, port:3000")
    run_parser.add_argument("--interval", type=float, default=2.0, help="poll interval in seconds")
    run_parser.add_argument("cmd", nargs=argparse.REMAINDER, help="command after --")
    run_parser.set_defaults(func=command_run)

    watch_parser = sub.add_parser("watch", help="watch a terminal session without running a command")
    watch_parser.add_argument("--name", default="", help="session label")
    watch_parser.add_argument("--resource", action="append", default=[], help="resource token")
    watch_parser.add_argument("--interval", type=float, default=2.0, help="poll interval in seconds")
    watch_parser.set_defaults(func=command_watch)

    status_parser = sub.add_parser("status", help="show active sessions and conflicts")
    status_parser.set_defaults(func=command_status)

    stop_parser = sub.add_parser("stop", help="remove one or all sessions")
    stop_parser.add_argument("--session-id", default="", help="session id to stop")
    stop_parser.add_argument("--all", action="store_true", help="remove all sessions")
    stop_parser.set_defaults(func=command_stop)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "run" and args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
