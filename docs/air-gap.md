# The air-gap proof

*Part of [NIGHTGLASS](../README.md) — the air-gapped SAR intelligence assistant.*

No route to the internet, and the model answers anyway — in the same session. Reproduce with
`make air-gap-proof`.

```
NIGHTGLASS — air-gap proof   2026-08-03T17:29:00Z

----------------------------------------------------------------------
$ docker compose exec api curl -m 5 https://example.com
curl: (6) Could not resolve host: example.com

✓ blocked (curl exit 6)

----------------------------------------------------------------------
$ docker compose exec api curl -m 5 http://ollama:11434/api/tags   # inside the enclave
models: bge-m3:latest, qwen2.5:14b-instruct-q4_K_M

----------------------------------------------------------------------
$ chat completion against the local model
Synthetic Aperture Radar (SAR) is a radar technique that creates high-resolution
images regardless of weather or lighting conditions by synthesizing a large
antenna aperture, which allows it to operate effectively both day and night.

----------------------------------------------------------------------
No route to the internet. Inference ran anyway.
```

The failure mode is worth reading precisely. It is **`Could not resolve host`, not a timeout** —
an internal network's embedded DNS does not forward external queries, so the name never resolves
and no packet is ever sent. A firewalled system drops your traffic; this one has nowhere to send
it. Meanwhile `ollama`, `qdrant` and `postgis` resolve normally on the same network, and both
models are loaded and serving.
