"""Build the tiny stand-in ONNX model used by CI perception tests.

Why a stand-in
--------------
Per ADR 0004 and the Phase 3 spec (Tech Note 3), CI runs perception
tests against a *deterministic* tiny ONNX model committed to the repo
so test runs need neither MinIO nor network. This script generates
that model.

Architecture
------------
- Input:  ``(1, 3, 64, 64)`` float32 (NCHW).
- Output: ``(1, 1, 64, 64)`` float32 (single-class segmentation
  logits).

The implementation is a single ``Conv`` op with a fixed 3-channel-to-1
3x3 kernel that produces visibly different outputs for visibly
different inputs but is otherwise meaningless. That's enough for unit
tests: the perception scorer needs the inference to *run*, not to be
accurate.

The model is built with the ``onnx`` library directly (no ``torch``
dependency) so devs can regenerate the stand-in without pulling the
~1.5 GB ``model-export`` dep group.

Run with:

    uv run --group onnx-tools python tools/perception/build_standin_onnx.py

The committed artifact lives at
``tests/fixtures/perception/standin.onnx``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def build_model() -> onnx.ModelProto:
    """Single-Conv stand-in: ``(1, 3, 64, 64) -> (1, 1, 64, 64)``."""
    input_tensor = helper.make_tensor_value_info(
        name="input",
        elem_type=TensorProto.FLOAT,
        shape=[1, 3, 64, 64],
    )
    output_tensor = helper.make_tensor_value_info(
        name="output",
        elem_type=TensorProto.FLOAT,
        shape=[1, 1, 64, 64],
    )

    # 3x3 kernel, 3 in-channels → 1 out-channel. Weights chosen so red
    # channel dominates output (an arbitrary but deterministic choice).
    rng = np.random.default_rng(seed=42)
    weight_data = rng.standard_normal(size=(1, 3, 3, 3), dtype=np.float32) * 0.1
    weight_init = numpy_helper.from_array(weight_data, name="conv.weight")
    bias_init = numpy_helper.from_array(np.zeros((1,), dtype=np.float32), name="conv.bias")

    conv_node = helper.make_node(
        op_type="Conv",
        inputs=["input", "conv.weight", "conv.bias"],
        outputs=["output"],
        kernel_shape=[3, 3],
        pads=[1, 1, 1, 1],
        strides=[1, 1],
        name="standin_conv",
    )

    graph = helper.make_graph(
        nodes=[conv_node],
        name="streetsense_standin_perception",
        inputs=[input_tensor],
        outputs=[output_tensor],
        initializer=[weight_init, bias_init],
    )

    model = helper.make_model(
        graph,
        producer_name="streetsense-build-standin-onnx",
        opset_imports=[helper.make_opsetid("", 17)],
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="tests/fixtures/perception/standin.onnx",
        help="Where to write the .onnx artifact (relative to repo root).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    out_path = repo_root / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    model = build_model()
    out_path.write_bytes(model.SerializeToString())
    print(f"Wrote {out_path.relative_to(repo_root)} ({out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
