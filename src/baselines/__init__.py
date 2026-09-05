"""Baseline decision policies and the ablation harness runner.

Supplementary, isolated from the locked recovery pipeline: these policies
exist only so the evaluation harness can compare the real DecisionEngine
against simpler, deliberately-naive decision rules. Nothing here is
imported by ``recoveryloop`` itself.
"""
