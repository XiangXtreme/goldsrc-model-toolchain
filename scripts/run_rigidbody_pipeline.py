#!/usr/bin/env python3
"""Compatibility wrapper for legacy rigid-body pipeline specifications."""

from run_model_pipeline import main


if __name__ == "__main__":
    raise SystemExit(main(cache_name=".goldsrc-rigidbody-pipeline.json", report_name="rigidbody_pipeline_report.json"))
