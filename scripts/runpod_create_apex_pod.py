#!/usr/bin/env python3
"""Create an Apex Ollama pod on RunPod from CLI.

Usage:
  1) Export API key in your shell session:
     PowerShell: $env:RUNPOD_API_KEY = "<your_key>"
  2) Run:
     python scripts/runpod_create_apex_pod.py --wait
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import runpod


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create Apex pod on RunPod")
    parser.add_argument("--name", default="apex-llm", help="Pod name")
    parser.add_argument(
        "--image",
        default="ollama/ollama:0.11.6",
        help="Container image",
    )
    parser.add_argument(
        "--gpu",
        default="NVIDIA GeForce RTX 4090",
        help="GPU type id as expected by RunPod SDK",
    )
    parser.add_argument(
        "--cloud-type",
        default="SECURE",
        choices=["SECURE", "COMMUNITY", "ALL"],
        help="RunPod cloud type",
    )
    parser.add_argument("--gpu-count", type=int, default=1, help="Number of GPUs")
    parser.add_argument("--volume-gb", type=int, default=120, help="Persistent volume size")
    parser.add_argument(
        "--container-disk-gb",
        type=int,
        default=15,
        help="Ephemeral container disk size",
    )
    parser.add_argument(
        "--ports",
        default="11434/http,8000/http,22/tcp",
        help="Exposed ports",
    )
    parser.add_argument(
        "--volume-mount-path",
        default="/workspace",
        help="Where the persistent volume is mounted",
    )
    parser.add_argument(
        "--no-public-ip",
        action="store_true",
        help="Disable public IP assignment",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait until pod reaches a running state",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=900,
        help="Max seconds to wait when --wait is set",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=10,
        help="Polling interval seconds when --wait is set",
    )
    return parser


def _status_from_pod(pod: dict[str, Any]) -> str:
    return str(
        pod.get("desiredStatus")
        or pod.get("lastStatus")
        or pod.get("status")
        or "unknown"
    )


def _print_connection_hints(pod: dict[str, Any]) -> None:
    runtime = pod.get("runtime") or {}
    ports = runtime.get("ports") or []
    if ports:
        print("Runtime ports:")
        for p in ports:
            public_ip = p.get("ip") or runtime.get("publicIp") or "?"
            public_port = p.get("publicPort") or "?"
            private_port = p.get("privatePort") or p.get("port") or "?"
            protocol = p.get("protocol") or "tcp"
            print(f"  - {public_ip}:{public_port} -> {private_port}/{protocol}")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    api_key = os.getenv("RUNPOD_API_KEY", "").strip()
    if not api_key:
        print(
            "RUNPOD_API_KEY is not set. In PowerShell: $env:RUNPOD_API_KEY = \"<key>\"",
            file=sys.stderr,
        )
        return 2

    runpod.api_key = api_key

    env = {
        "OLLAMA_FLASH_ATTENTION": "1",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_BATCH_SIZE": "512",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        # Persist models on the mounted volume.
        "OLLAMA_MODELS": f"{args.volume_mount_path.rstrip('/')}/.ollama",
    }

    try:
        created = runpod.create_pod(
            name=args.name,
            image_name=args.image,
            gpu_type_id=args.gpu,
            cloud_type=args.cloud_type,
            support_public_ip=not args.no_public_ip,
            start_ssh=True,
            gpu_count=args.gpu_count,
            volume_in_gb=args.volume_gb,
            container_disk_in_gb=args.container_disk_gb,
            ports=args.ports,
            volume_mount_path=args.volume_mount_path,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to create pod: {exc}", file=sys.stderr)
        return 1

    pod_id = created.get("id")
    if not pod_id:
        print("Pod creation response did not include an id:")
        print(json.dumps(created, indent=2, ensure_ascii=True))
        return 1

    print(f"Pod created: {pod_id}")
    print(f"Initial status: {_status_from_pod(created)}")

    if not args.wait:
        print("Tip: run again with --wait to block until running.")
        return 0

    deadline = time.time() + max(args.wait_timeout, 1)
    while time.time() < deadline:
        try:
            pod = runpod.get_pod(pod_id)
        except Exception as exc:  # noqa: BLE001
            print(f"Polling failed: {exc}")
            time.sleep(max(args.poll_interval, 1))
            continue

        status = _status_from_pod(pod)
        print(f"Status: {status}")

        if status.lower() in {"running", "ready"}:
            _print_connection_hints(pod)
            return 0

        if status.lower() in {"failed", "terminated", "stopped"}:
            print("Pod entered non-running terminal state:")
            print(json.dumps(pod, indent=2, ensure_ascii=True))
            return 1

        time.sleep(max(args.poll_interval, 1))

    print("Timed out waiting for pod to become running.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
