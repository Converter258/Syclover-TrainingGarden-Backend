from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ContainerError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartedContainer:
    container_id: str
    public_port: int


class DockerService:
    def __init__(self, mode: str = "cli"):
        self.mode = mode

    def start(
        self, *, image: str, internal_port: int, name: str, user_id: str, challenge_id: str
    ) -> StartedContainer:
        if not IMAGE_PATTERN.fullmatch(image) or not CONTAINER_PATTERN.fullmatch(name):
            raise ContainerError("Unsafe container image or name")
        if self.mode == "mock":
            return StartedContainer(container_id=f"mock-{name}", public_port=10000 + internal_port % 50000)
        container_id = self._run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--label",
                "syclover.training-garden=true",
                "--label",
                f"syclover.user={user_id}",
                "--label",
                f"syclover.challenge={challenge_id}",
                "--memory",
                "512m",
                "--cpus",
                "1.0",
                "--pids-limit",
                "256",
                "--security-opt",
                "no-new-privileges",
                "-p",
                f"127.0.0.1::{internal_port}",
                image,
            ],
            timeout=90,
        ).strip()
        port_output = self._run(
            ["docker", "port", container_id, f"{internal_port}/tcp"], timeout=10
        ).strip()
        try:
            public_port = int(port_output.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            self.stop(container_id)
            raise ContainerError(f"Docker returned an invalid port mapping: {port_output}") from exc
        return StartedContainer(container_id=container_id, public_port=public_port)

    def stop(self, container_id: str) -> None:
        if not CONTAINER_PATTERN.fullmatch(container_id):
            raise ContainerError("Unsafe container identifier")
        if self.mode == "mock":
            return
        self._run(["docker", "stop", "--time", "5", container_id], timeout=15)

    def apply_asset(self, container_id: str, path: Path, kind: str) -> str:
        if not CONTAINER_PATTERN.fullmatch(container_id):
            raise ContainerError("Unsafe container identifier")
        if self.mode == "mock":
            return f"Mock mode: {kind} {path.name} accepted for {container_id}."
        target = f"/tmp/syclover-{path.name}"
        self._run(["docker", "cp", str(path), f"{container_id}:{target}"], timeout=20)
        if kind in {"check_script", "fix_script"}:
            command = ["docker", "exec", container_id, "/bin/sh", target]
        elif kind == "patch":
            command = [
                "docker",
                "exec",
                container_id,
                "/bin/sh",
                "-c",
                f"cd /app && patch -p1 < {target}",
            ]
        else:
            raise ContainerError("Only patches and fix scripts can be applied")
        return self._run(command, timeout=30)

    @staticmethod
    def _run(command: list[str], timeout: int) -> str:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise ContainerError("Docker CLI is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise ContainerError("Docker operation timed out") from exc
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise ContainerError(output or f"Docker exited with code {result.returncode}")
        return output
