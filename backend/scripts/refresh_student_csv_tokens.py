"""Refresh access tokens in a local student load-test CSV through the login API."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import tempfile
from pathlib import Path

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=Path("load-data/students.csv"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


async def refresh(args: argparse.Namespace) -> int:
    with args.file.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    required = {"login_name", "password", "token"}
    if not required.issubset(fieldnames):
        raise RuntimeError(f"CSV must contain columns: {', '.join(sorted(required))}")
    if args.concurrency <= 0:
        raise RuntimeError("--concurrency must be positive")
    if args.limit < 0:
        raise RuntimeError("--limit must not be negative")

    semaphore = asyncio.Semaphore(args.concurrency)
    refresh_count = min(args.limit, len(rows)) if args.limit else len(rows)
    tokens: list[str | None] = [row["token"] for row in rows]

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=httpx.Timeout(30.0),
        limits=httpx.Limits(
            max_connections=args.concurrency,
            max_keepalive_connections=args.concurrency,
        ),
    ) as client:

        async def login_one(index: int, row: dict[str, str]) -> None:
            async with semaphore:
                response = await client.post(
                    "/api/v1/auth/login",
                    json={
                        "login_name": row["login_name"],
                        "password": row["password"],
                    },
                )
                response.raise_for_status()
                token = response.json().get("access_token")
                if not isinstance(token, str) or not token:
                    raise RuntimeError(f"login row {index + 2} returned no access_token")
                tokens[index] = token

        results = await asyncio.gather(
            *(login_one(index, rows[index]) for index in range(refresh_count)),
            return_exceptions=True,
        )

    failures = [result for result in results if isinstance(result, BaseException)]
    if failures:
        raise RuntimeError(
            f"token refresh failed for {len(failures)} of {refresh_count} rows; "
            f"first error: {failures[0]}"
        )

    for row, token in zip(rows, tokens, strict=True):
        row["token"] = token or ""

    fd, temp_name = tempfile.mkstemp(
        prefix=f"{args.file.stem}-",
        suffix=".tmp",
        dir=args.file.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_name, args.file)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return refresh_count


async def main() -> None:
    args = parse_args()
    refreshed = await refresh(args)
    print(f"refreshed_tokens={refreshed}")
    print(f"output={args.file.resolve()}")
    print("database_modified=false")


if __name__ == "__main__":
    asyncio.run(main())
