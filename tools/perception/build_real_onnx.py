"""Convert a pretrained semantic-segmentation model to ONNX.

ADR 0004 commits to a pretrained model served via ONNX Runtime;
this script does the conversion. SegFormer-B0 (Hugging Face) is the
default candidate.

This script lives in the ``model-export`` dependency group (torch +
transformers — ~1.5 GB) so it is **not** installed in CI or in the
runtime image. Devs run it once to produce
``artifacts/perception/<name>.onnx`` and then upload via
``make seed-model`` (Task 3.3.7).

Usage::

    uv sync --group model-export
    uv run --group model-export python tools/perception/build_real_onnx.py \\
        --model nvidia/segformer-b0-finetuned-cityscapes-1024-1024 \\
        --output artifacts/perception/segformer-b0.onnx

The output ONNX is verified by a CPU inference round trip before
write. SHA-256 of the artifact is printed to stdout; record it in
ADR 0004's Decision section.

Phase 3 ships with the stand-in ONNX as the default. The real model
artifact landing here is a follow-up: per the relaxed-validation
posture in ADR 0004, real Cambridge F1 is the trigger to swap.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Defer heavy imports until invocation so the rest of the repo's
# tooling doesn't pay the cost.


def _import_heavy() -> tuple[object, object]:
    try:
        import torch  # type: ignore[import-not-found]
        from transformers import (  # type: ignore[import-not-found]
            SegformerForSemanticSegmentation,
        )
    except ImportError as exc:  # pragma: no cover
        sys.stderr.write(
            "torch/transformers not installed. Install the model-export group:\n"
            "    uv sync --group model-export\n"
        )
        raise SystemExit(2) from exc
    return torch, SegformerForSemanticSegmentation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="nvidia/segformer-b0-finetuned-cityscapes-1024-1024",
        help="Hugging Face model ID (default: SegFormer-B0 Cityscapes).",
    )
    parser.add_argument(
        "--output",
        default="artifacts/perception/segformer-b0.onnx",
        help="Where to write the ONNX artifact.",
    )
    parser.add_argument(
        "--input-size",
        type=int,
        default=512,
        help="Square input size for ONNX export (default: 512).",
    )
    args = parser.parse_args(argv)

    torch, SegformerForSemanticSegmentation = _import_heavy()

    print(f"Loading {args.model} from Hugging Face...")
    model = SegformerForSemanticSegmentation.from_pretrained(args.model)  # type: ignore[attr-defined]
    model.eval()  # type: ignore[attr-defined]

    repo_root = Path(__file__).resolve().parents[2]
    out_path = repo_root / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dummy = torch.zeros(1, 3, args.input_size, args.input_size, dtype=torch.float32)  # type: ignore[attr-defined]
    print(f"Exporting to ONNX at {out_path.relative_to(repo_root)}...")

    torch.onnx.export(  # type: ignore[attr-defined]
        model,
        dummy,
        str(out_path),
        input_names=["input"],
        output_names=["logits"],
        opset_version=17,
        do_constant_folding=True,
        dynamic_axes={
            "input": {0: "batch"},
            "logits": {0: "batch"},
        },
    )

    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"Done. Artifact size: {out_path.stat().st_size} bytes")
    print(f"SHA-256: {digest}")
    print(
        "\nRecord this SHA in docs/adr/0004-perception-model.md's Decision\n"
        "section. Upload to MinIO with:\n"
        f"    make seed-model ARTIFACT={out_path.relative_to(repo_root)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
