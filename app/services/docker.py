from __future__ import annotations

import asyncio
import ipaddress
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
FLAG_PATTERN = re.compile(r"^[A-Za-z0-9_{}.\-]{1,480}$")
CONTAINER_LABEL = "syclover.training-garden=true"


class ContainerError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartedContainer:
    container_id: str
    public_port: int


def _validated_bind_address(value: str) -> str:
    """Accept only literal IP addresses so the published port cannot be redirected."""
    candidate = value.strip().strip("[]")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError as exc:
        raise ContainerError(f"Invalid instance bind address: {value}") from exc


class DockerService:
    def __init__(self, mode: str = "cli"):
        self.mode = mode
        self._buildx: bool | None = None

    def start(
        self,
        *,
        image: str,
        internal_port: int,
        name: str,
        user_id: str,
        challenge_id: str,
        bind_address: str = "127.0.0.1",
        port_range: range | None = None,
        flag: str | None = None,
    ) -> StartedContainer:
        if not IMAGE_PATTERN.fullmatch(image) or not CONTAINER_PATTERN.fullmatch(name):
            raise ContainerError("Unsafe container image or name")
        if flag is not None and not FLAG_PATTERN.fullmatch(flag):
            raise ContainerError("Unsafe instance flag value")
        if self.mode == "mock":
            return StartedContainer(container_id=f"mock-{name}", public_port=10000 + internal_port % 50000)
        bind = _validated_bind_address(bind_address)
        if port_range is not None and len(port_range) == 0:
            raise ContainerError("The configured instance port range is empty")
        container_id = self._run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--label",
                CONTAINER_LABEL,
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
                "--env",
                f"FLAG={flag}" if flag else "FLAG=",
                "-p",
                self._port_spec(bind, internal_port, port_range),
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
        if port_range is not None and public_port not in port_range:
            self.stop(container_id)
            raise ContainerError(
                f"Docker published port {public_port}, which is outside the configured range "
                f"{port_range.start}-{port_range.stop - 1}"
            )
        return StartedContainer(container_id=container_id, public_port=public_port)

    @staticmethod
    def _port_spec(bind: str, internal_port: int, port_range: range | None) -> str:
        host_bind = f"[{bind}]" if ":" in bind else bind
        if port_range is not None and len(port_range) == 1:
            return f"{host_bind}:{port_range.start}:{internal_port}"
        return f"{host_bind}::{internal_port}"

    async def build(self, *, context: Path, image: str, on_output=None) -> str:
        """Build an image, forwarding each output chunk to ``on_output`` when given."""
        if not IMAGE_PATTERN.fullmatch(image):
            raise ContainerError("Unsafe image tag")
        if not (context / "Dockerfile").is_file():
            raise ContainerError("Dockerfile is missing from the build context")
        if self.mode == "mock":
            message = f"Mock mode: built {image} from {context.name}/Dockerfile."
            if on_output:
                on_output(message + "\n")
            return message
        command = ["docker", "build"]
        if await self._supports_buildx():
            # BuildKit needs --progress plain to emit line-oriented, streamable logs.
            command += ["--progress", "plain"]
        command += ["--pull", "--tag", image, str(context)]
        return await self._run_streaming(command, timeout=600, on_output=on_output)

    async def _supports_buildx(self) -> bool:
        """Detect the buildx plugin once; the legacy builder rejects ``--progress``."""
        if self._buildx is None:
            try:
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    "buildx",
                    "version",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                self._buildx = (await asyncio.wait_for(process.wait(), timeout=10)) == 0
            except (OSError, TimeoutError):
                self._buildx = False
        return self._buildx

    @staticmethod
    async def _run_streaming(command: list[str], timeout: int, on_output=None) -> str:
        """Run a command, emitting output as it arrives, and return the whole log."""
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            raise ContainerError("Docker CLI is not installed") from exc
        collected: list[str] = []

        async def consume() -> None:
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                text = chunk.decode(errors="replace")
                collected.append(text)
                if on_output:
                    on_output(text)

        try:
            await asyncio.wait_for(consume(), timeout=timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ContainerError("Docker operation timed out") from exc
        await process.wait()
        output = "".join(collected).strip()
        if process.returncode != 0:
            raise ContainerError(output or f"Docker exited with code {process.returncode}")
        return output

    def stop(self, container_id: str) -> None:
        if not CONTAINER_PATTERN.fullmatch(container_id):
            raise ContainerError("Unsafe container identifier")
        if self.mode == "mock":
            return
        self._run(["docker", "stop", "--time", "5", container_id], timeout=15)

    def list_managed_containers(self) -> list[dict[str, str]]:
        """Challenge containers carrying the platform label, as ``id``/``name`` pairs."""
        if self.mode == "mock":
            return []
        # ``--quiet`` would silently disable ``--format``, so request both columns directly.
        output = self._run(
            [
                "docker",
                "ps",
                "--no-trunc",
                "--filter",
                f"label={CONTAINER_LABEL}",
                "--format",
                "{{.ID}}\t{{.Names}}",
            ],
            timeout=15,
        )
        containers: list[dict[str, str]] = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) != 2 or not parts[0].strip():
                continue
            containers.append({"id": parts[0].strip(), "name": parts[1].strip()})
        return containers

    def container_exists(self, container_id: str) -> bool:
        if not CONTAINER_PATTERN.fullmatch(container_id):
            raise ContainerError("Unsafe container identifier")
        if self.mode == "mock":
            return True
        try:
            self._run(
                ["docker", "inspect", "--format", "{{.State.Running}}", container_id], timeout=15
            )
        except ContainerError:
            return False
        return True

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

    @staticmethod
    async def _run_async(command: list[str], timeout: int) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ContainerError("Docker CLI is not installed") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ContainerError("Docker operation timed out") from exc
        output = (stdout + stderr).decode(errors="replace").strip()
        if process.returncode != 0:
            raise ContainerError(output or f"Docker exited with code {process.returncode}")
        return output
