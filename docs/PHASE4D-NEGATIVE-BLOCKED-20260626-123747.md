# Phase 4-D Negative Dry Run: blocked

This file creates a non-review.json commit for testing a real review.json with verdict blocked.

Expected result:
- real-review-shadow-gate: denied, workflow success
- production-gate-dry-run: denied, workflow success
- production-gate-dry-run-result artifact: uploaded

Timestamp: 20260626-123747
