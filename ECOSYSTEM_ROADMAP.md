# Roadmap — vtskit

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **納品 (Delivery)**, status `active`, optional component.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v0.4.0, DVD-Video authoring library (no CLI). Python 3.11+ stdlib, unittest, CI on Ubuntu + macOS. Optional component (installed with --with-optional). oncemore vendors this library.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| DVD-Video作成 | done |  |

## Wiring

Not reachable from the agent; encodes with ffmpeg directly.

## Steps

- [x] **1. (optional) Contract in the OS schema** — skill_id, version, provides[]
  - Done when: validates against the OS schema
- [x] **2. Bitrate planner and session chapters** — 1-4 h programmes on DVD-5 / DVD-9 with a stated quality floor and a warning below it, chapters per session from a chapter list. Field review 2026-11-05 (Velisa ROADMAP, third round) ([#41](https://github.com/kajisho5/vtskit/pull/41))
  - なぜ要るか：DVD での記録納品を求める主催者に、収まるかどうかを先に答えられる
  - Done when: a 3 h chapter list plans on DVD-9 or reports the shortfall

## Boundaries

Vendored by oncemore (`oncemore/_vendor/`).

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
