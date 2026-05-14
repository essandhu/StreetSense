"""Upload-or-skip a perception model artifact to MinIO.

Reads a local ONNX file, computes its SHA-256, derives a
``perception_model_version`` of the form ``{name}-{short_sha}``, and
uploads to MinIO under
``streetsense-models/{perception_model_version}/{name}.onnx``.
Idempotent: re-running against the same artifact is a no-op (the
object key already exists).

Also registers a ``perception_model`` row in Postgres'
``data_sources`` table so ``/admin/freshness`` can report on it.

Usage::

    make seed-model
    # or manually:
    uv run python tools/perception/seed_model.py \\
        --artifact tests/fixtures/perception/standin.onnx \\
        --name lane-marking-standin
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg
from minio import Minio
from psycopg.types.json import Jsonb


def _minio_from_env() -> Minio:
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ.get("MINIO_ROOT_USER", "streetsense"),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "streetsense"),
        secure=False,
    )


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set; source .env first.")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        default="tests/fixtures/perception/standin.onnx",
        help="Local ONNX file to upload (default: stand-in).",
    )
    parser.add_argument(
        "--name",
        default="lane-marking-standin",
        help="Short artifact name (used in object key + freshness metadata).",
    )
    parser.add_argument(
        "--bucket",
        default="streetsense-models",
        help="MinIO bucket (default: streetsense-models).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    artifact_path = repo_root / args.artifact
    if not artifact_path.exists():
        sys.exit(f"Artifact not found: {artifact_path}")

    payload = artifact_path.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()
    short_sha = sha[:12]
    perception_model_version = f"{args.name}-{short_sha}"
    object_key = f"{perception_model_version}/{args.name}.onnx"

    client = _minio_from_env()
    if not client.bucket_exists(args.bucket):
        sys.exit(f"Bucket {args.bucket} does not exist; run `docker compose up -d` first.")

    try:
        client.stat_object(args.bucket, object_key)
        print(f"Skip: {object_key} already exists in {args.bucket}.")
    except Exception:
        import io

        client.put_object(
            args.bucket,
            object_key,
            io.BytesIO(payload),
            length=len(payload),
            content_type="application/octet-stream",
        )
        print(f"Uploaded {object_key} ({len(payload)} bytes) to {args.bucket}.")

    # Register/refresh data_sources row.
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO data_sources (name, last_ingested_at, metadata)
                VALUES (
                    'perception_model',
                    now(),
                    %s
                )
                ON CONFLICT (name) DO UPDATE
                SET last_ingested_at = EXCLUDED.last_ingested_at,
                    metadata = EXCLUDED.metadata
                """,
                (
                    Jsonb(
                        {
                            "kind": "model",
                            "perception_model_version": perception_model_version,
                            "object_key": object_key,
                            "bucket": args.bucket,
                            "sha256": sha,
                            "name": args.name,
                        }
                    ),
                ),
            )
        conn.commit()
    print(f"data_sources.perception_model.metadata updated -> version={perception_model_version}")
    print(f"perception_model_version: {perception_model_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
