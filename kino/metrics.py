"""Prometheus metrics (CLAUDE.md section 14)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

webhook_requests = Counter("kino_webhook_requests_total", "Webhook requests", ["result"])
images = Counter("kino_images_total", "Images by status", ["status"])
duplicates = Counter("kino_duplicates_total", "Duplicate images skipped")
pipeline_seconds = Histogram("kino_pipeline_seconds", "Pipeline stage duration", ["stage"])
faces_detected = Counter("kino_faces_detected_total", "Faces detected", ["tier", "source"])
matches = Counter("kino_matches_total", "Matches", ["kid", "decision", "method"])
review_queue_size = Gauge("kino_review_queue_size", "Pending review items")
queue_depth = Gauge("kino_queue_depth", "Pending pipeline jobs")
gallery_refs = Gauge("kino_gallery_refs", "Reference faces", ["kid", "source"])
forwards = Counter("kino_forwards_total", "Forwards by status", ["status"])
forward_queue_depth = Gauge("kino_forward_queue_depth", "Pending forwards")
digests_sent = Counter("kino_digests_sent_total", "Review digests sent")
auth = Counter("kino_auth_total", "Auth attempts", ["method", "result"])
model_info = Gauge("kino_model_info", "Loaded models", ["name", "version", "license"])
