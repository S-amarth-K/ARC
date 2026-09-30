# ARC — Attention & Risk Compression

**A Smart Operator Assistant for Caterpillar heavy machinery.**
The machine takes on more of the complexity so the operator has less to deal with. All the intelligence runs through one **Arbitration Engine**. For each event it decides whether to act silently, log for the supervisor, show an ambient icon, show one plain-language action, or intervene immediately. Everything is logged. Only what reaches the operator is filtered.

```
telemetry ─► features ─► personal baseline (z-scores) ─┐
                      ─► risk-trajectory model (XGBoost + SHAP) ─┼─► ARBITRATION ENGINE ─► event_log (everything)
                      ─► task-time model (quantile GBM) ─┘      (5 tiers + Gate)   └─► HUD (at most one thing)
                                                                        ▲                         │
                                     calibration (per operator) ◄── feedback: Loop 1 "did it work?" + Loop 2 "did they understand?"
```

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the tier logic and feedback loops.

---

## Quick start

Requirements: Python 3.11+, Node 20+.

```bash
# 1. Python env + dependencies
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Build the data: synthetic dataset -> train models -> back-fill history through the engine (~1-2 min)
#    (stop the backend first if it's running: it holds data/arc.db open, and Windows won't let setup replace it)
python -m backend.setup

# 3. Frontend dependencies (once)
cd frontend
npm install
cd ..
```

### Run (three commands, three terminals)

| What | Command | Where |
|---|---|---|
| **Backend** (FastAPI + WebSockets) | `python -m backend.server` | http://localhost:8000 (API docs at `/docs`) |
| **Frontend** (React + Tailwind) | `cd frontend` then `npm run dev` | http://localhost:5173 |
| **Demo replay** | `python -m backend.demo` *(or click **▶ Run demo** in the HUD)* | narrates stages in the terminal |

Open **http://localhost:5173/hud/M02?judge=1** before starting the demo. That's the operator's cab view, with the judge panel on the right.

Optional:

```bash
python -m backend.simulator                    # replay a historical shift for M01, M03-M06 as live telematics
python -m backend.simulator --shift S07 --speed 5 --machines M01 M03 --from-hour 5
```

**Gemini agents:** copy `.env.example` to `.env` and set `GEMINI_API_KEY` (free key at https://aistudio.google.com/apikey). Without a key, all three agents fall back to deterministic answers built from the same database, so the demo works offline.

**Single-process mode:** run `npm run build` inside `frontend/` and the backend will serve the built UI at http://localhost:8000.

---

## The demo (≈90 s)

The demo runs on machine **M02**, operator **OP04 "Op. Dube"**: aggressive driving style, and a history of dismissing nudges. Every scripted tick goes through the same `Engine.on_tick()` path as live telemetry.

1. **Quiet:** 5h33m into the shift, normal digging, ambient strip green. ARC switches to the rear camera once without telling anyone (tier 1). It appears in the judge panel's arbitration trail as "silent".
2. **Risk builds:** rain arrives, the machine repositions toward the crew area, controls get sharper, and an object closes in from the rear. The judge panel shows the risk curve climbing past OP04's **personal** soft threshold (0.65) and the top SHAP contributors.
3. **Tier 4:** *"Crew area close. Slow to walking pace."* It plays with audio because calibration moved OP04 to "fewer but louder" nudges.
4. **Tier 5:** risk crosses the **hard floor (0.85)** → **STOP. Person close to the machine.** It uses the fixed vocabulary, audio, and hazard stripes. The operator can't dismiss it, only acknowledge it (press **ACKNOWLEDGE**, tap **Why?**, or say "why" / "got it" into the mic).
5. **Recovery:** the machine stops and the person clears. Five simulated minutes later both feedback loops are scored and shown in the judge panel: *Loop 1: corrective ✓ in 30 s, back to baseline ✓. Loop 2: acknowledged in N s.* Calibration is then re-evaluated.
6. **Break → foreman trade-off:** the task screen unlocks (it's locked while the machine operates). The task-time model predicts an ~80 min overrun. **"Push pace +41%"** is costed with the real risk model (12% → 25%). ARC recommends moving a task to M01, which has slack.
7. **Supervisor:** click **Open supervisor dashboard →**. It shows every tier, held cards, the checklist mismatch that was never shown in the cab, per-operator comprehension and calibration history, training gaps, and the query agent.

---

## What's in the box

| Phase | Where | Notes |
|---|---|---|
| 1 Synthetic dataset | `backend/data/generate_dataset.py` | 6 machines, 8 operators (behavioural × comprehension archetypes), 12 historical shifts + today, 30 s ticks → 69,120 telemetry rows. Injects fatigue drift, seatbelt-off at idle, hot-zone proximity, weather blocks, checklist mismatches, and **risk episodes** whose precursors escalate for 5–15 min before an unsafe event. |
| 2 Features + models | `backend/ml/` | One `compute_features()` shared by training and serving. Personal baselines → z-scores. XGBoost risk model with per-prediction TreeSHAP. Quantile GBM task-time model. Derived effort-to-output, attention budget, task-delay probability. |
| 3 Arbitration Engine | `backend/arbitration.py`, `backend/pipeline.py` | `route()` is five readable if-statements. `Gate` enforces one-thing-at-a-time. Deterministic hard rules. |
| 4 Operator HUD | `frontend/src/pages/OperatorHUD.jsx`, `components/hud.jsx` | Ambient strip, tier-4 card with collapsed "why", tier-5 overlay that must be acknowledged, voice, locked task/training/briefing screens. |
| 5 Supervisor dashboard | `frontend/src/pages/Supervisor.jsx` | Fleet trends, event log with signal snapshots, checklist mismatches, comprehension and calibration per operator, training gaps, live feed. |
| 6 Gemini agents | `backend/agents.py` | Three separate, narrow roles (see ARCHITECTURE.md). |
| 7 Feedback + calibration | `backend/pipeline.py` (`FeedbackWatcher`), `backend/calibration.py` | Rule-based policy update, documented as a contextual bandit. Full audit trail in `calibration_history`. |
| 8 Demo mode | `backend/demo.py` | Reactive: waits for a human to acknowledge the STOP. |
| Message library | `backend/messages.py`, `frontend/public/audio/*.wav` | 23 fixed lines. The 13 cab-facing lines are pre-recorded (`scripts/make_audio.ps1`, Windows SAPI). Web Speech API is the fallback. |

### Numbers (from `python -m backend.setup`, seed 42)

| | |
|---|---|
| Risk model, held-out shifts S10–S12 | ROC-AUC **0.72**, PR-AUC **0.30** (base rate 0.085) |
| Task-time model, held-out shifts | MAE **7.8 min** vs **12.7 min** for the planner's own estimates |
| Early warning (back-filled history, out-of-fold scores) | **~61%** of real unsafe events had a tier-4/5 warning in the prior 15 min, median lead **~4.5 min** |
| Compression | 69,120 ticks evaluated → **~6.4** tier-4/5 messages per 8-hour machine-shift reach the operator; ~2,500 events logged for supervisors |

### Deviations from the requested stack

- None in substance. Tailwind **v4** (Vite plugin) and **Recharts** 3.
- LLM: Google Gemini (`gemini-2.5-flash` by default, via the `google-genai` SDK) for all three agents. The in-cab explainer runs with thinking off for latency. Override with `ARC_GEMINI_MODEL`.
- Pre-recorded audio is generated with the Windows speech engine rather than a human voice actor. Regenerate on Windows with `python -m backend.export_audio_lines` and then `scripts/make_audio.ps1`.

### Honest limitations

- All data is synthetic. The ML is real (trained, held-out evaluated, SHAP-explained), but it can only be as good as the generator's assumptions.
- The historical telemetry was generated without ARC in the loop, so **historical** operator reactions (the feedback_log for S01–S12) come from a simulated operator driven by each person's comprehension archetype (`backend/backfill.py`). Live and demo feedback is **measured** from telemetry and the HUD.
- Personal baselines are computed over each operator's full history. In production they would be an expanding window.
- Only the proximity training module has hand-written content. The others are titles and recommendations.

---

## Repo layout

```
backend/
  config.py            every threshold in one place
  site_model.py        machines, operators, hot zones, terrain
  data/generate_dataset.py
  ml/features.py  ml/train.py  ml/models.py
  messages.py          fixed message library
  arbitration.py       route() + Gate + hard rules  <- the core
  pipeline.py          Engine: tick -> features -> models -> route -> log -> surface -> feedback
  calibration.py       per-operator policy updates (Loop 1 + 2 -> thresholds/channel)
  backfill.py          runs the engine over history
  analytics.py         supervisor metrics, training gaps, foreman trade-off
  agents.py            3 Gemini agents + offline fallbacks
  server.py            FastAPI + WebSockets
  simulator.py         telematics stand-in (WebSocket client)
  demo.py              scripted demo
  setup.py             generate -> train -> back-fill
frontend/src/
  pages/OperatorHUD.jsx  pages/Supervisor.jsx  pages/Landing.jsx
  components/hud.jsx  JudgePanel.jsx  ShiftScreens.jsx  audio.js  voice.js
data/arc.db  models/   (produced by backend.setup)
```
