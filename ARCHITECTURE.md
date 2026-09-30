# ARC architecture: for judges

> **Design principle.** The goal isn't to make the machine *tell* the operator more. It's to make the machine *understand* more, so the operator has less to deal with.
> Automation may act silently on the operator's behalf. It must never silently discard information that the operator or a supervisor has a right to see: **everything is logged, and only what reaches the operator is filtered.**

## 1. One path for every signal

```
 simulator / telematics ──WS /ws/ingest──► Engine.on_tick()                                   backend/pipeline.py
                                             │
                        rolling 40-tick buffer per machine
                                             │
                         compute_features()   (same function used in training)            backend/ml/features.py
                                             │
          ┌──────────────────────────────────┼──────────────────────────────────┐
  personal baseline                 risk-trajectory model              task-time model
  jerk / cycle / idle z-scores      XGBoost: P(unsafe event ≤15 min)    quantile GBM (10/50/90%)
  vs THIS operator's normal         + TreeSHAP top contributors         → task-delay probability
          └──────────────────────────────────┼──────────────────────────────────┘
                                             ▼
                                arbitration.route(Signals, Policy)  ─► Decision(tier, message_key, reason)
                                             ▼
                                        Gate.admit()   one-at-a-time, cooldowns, attention budget
                                   ┌─────────┴──────────┐
                             event_log (ALWAYS)     HUD (only if surfaced)   ──WS /ws/hud/{machine}
                                   │                     │
                                   │              operator: ack / dismiss / "why"
                                   ▼                     ▼
                           FeedbackWatcher: Loop 1 (telemetry) + Loop 2 (HUD) ─► feedback_log
                                             ▼
                              calibration.update_policy(operator) ─► operator_calibration (+ history)
```

The live server, the scripted demo and the historical back-fill all call the **same** `Engine.step()`. So the two weeks of history on the supervisor dashboard came from exactly the logic judges see running live.

## 2. The five tiers

| Tier | Name | What the operator experiences | Logged? |
|---|---|---|---|
| **5** | Immediate intervention | Full-screen STOP, fixed vocabulary, pre-recorded audio repeating every 5 s. Can only be **acknowledged**, never dismissed. | yes |
| **4** | One plain-language action | One line (≤10 words, verb first). "Why?" is collapsed by default. Audio only if the operator's channel is `visual_audio`. | yes, including cards the Gate *held* |
| **3** | Ambient | One of three icons (safety / pace / machine) changes colour. No words. | yes (on change) |
| **2** | Supervisor only | Nothing. Checklist-vs-sensor mismatch, efficiency trends, foreman decisions. | yes |
| **1** | Act silently | Nothing. Auto-idle, hydraulic fan boost, rear camera auto-select. | yes (each action) |

### The router (`backend/arbitration.py`): the slide-worthy function

```python
def route(s: Signals, p: Policy) -> Decision:
    if s.hard_rule or s.risk_score >= HARD_SAFETY_THRESHOLD:     # 0.85: hard floor, never calibrated away
        return Decision(5, pick_t5(...))
    if s.risk_score >= p.soft_threshold:                          # personal: 0.35-0.65, set by the feedback loop
        return Decision(4, pick_t4(s.top_features))               # message chosen from the top SHAP feature
    if s.task_delay_prob > TASK_DELAY_THRESHOLD:                  # 0.70
        return Decision(4, "T4_TASK_DELAY")                       # "Foreman knows. Keep a safe pace." (never "go faster")
    if s.checklist_mismatch:        return Decision(2, "T2_CHECKLIST")
    if s.minor_inefficiency_trend:  return Decision(2, "T2_EFFICIENCY")
    if s.deviation_z > p.ambient_z: return Decision(3, "T3_SAFETY" | "T3_PACE")
    return Decision(1, None)
```

**Hard rules** are deterministic. No model can learn them away or calibration relax them:
person < 3 m while moving; seatbelt off at > 3 km/h; slope > 18° while moving.

**Message selection.** The tier-4/5 line is chosen from the model's top contributing feature (TreeSHAP), e.g. proximity features → *"Crew area close. Slow to walking pace."* All 23 lines are fixed and pre-written in `backend/messages.py`. Nothing in the cab is generated live.

### The Gate: "at most one thing at a time"

| Rule | Value |
|---|---|
| Tier 5 pre-empts any card. After acknowledgement, the same stop is **disarmed** until risk drops back below tier 4 (no nagging re-fires). | — |
| Only one tier-4 card on screen. Minimum gap between cards. | 5 min |
| Per-message cooldown. | 15 min |
| "Task running long" told to the operator at most once per… (every overrun is still logged for the foreman) | 3 h |
| Non-safety tier-4 lines (task delay, hydraulics, fatigue) are **deferred** when the attention budget is low. | budget < 40/100 |

A card the Gate holds is still written to `event_log` with `surfaced_to_operator = 0` and the reason (e.g. `held: another card already on screen`). The dashboard shows *"What reached the cab"* next to *"What ARC handled without interrupting"*.

**Attention budget** (0–100) = 100 − 30·weather severity − 30·closeness of nearest object − 20·time since break − 20·control roughness vs personal baseline.

## 3. The three intelligence modules

| Module | Model | Output used by the router |
|---|---|---|
| Personal baseline | Per-operator mean/std of jerk, relative cycle time, idle ratio (calm periods only) | `deviation_z` → tier 3. An aggressive operator's normal isn't flagged; a smooth operator getting rough is. |
| Risk trajectory | XGBoost on 26 engineered features (rolling jerk and trend, proximity min/trend/count, hot-zone distance, seatbelt, slope trend, weather, visibility, fatigue proxies, z-scores). Label: unsafe event within the next 15 min. | `risk_score` + top-3 TreeSHAP contributors (for message choice and "why") |
| Task time | Gradient boosting with quantile loss at 10/50/90% on task type, machine, operator archetype/tenure, weather, age, start hour | `task_delay_prob` = P(material overrun \| already running *t* min), plus the foreman trade-off |

Evaluated on held-out shifts: risk ROC-AUC 0.72 / PR-AUC 0.30 (base rate 0.085). Task MAE 7.8 min vs 12.7 min for the planner's own estimates. The back-fill uses **out-of-fold** scores, so historical metrics aren't memorised.

## 4. The dual feedback loop

Every **surfaced** tier-4/5 message gets a `FeedbackWatcher`. It is scored 5 simulated minutes later:

| Loop | Question | Signals (feedback_log) | How it's measured live |
|---|---|---|---|
| **1** | *Did it work?* | `corrective_action_taken`, `reaction_latency_s`, `metric_returned_to_baseline` | From telemetry **after** the message, using a check specific to each message: proximity → stopped or the gap opened; seatbelt → buckled; smooth → jerk down 15%; fatigue → rested ≥ 2 ticks; task delay → the operator did **not** start rushing. "Baseline" = risk < 0.8 × soft threshold and jerk back to their normal. |
| **2** | *Did they understand?* | `ack_response` (acknowledged / dismissed / no_response), `ack_latency_s`, `why_drawer_opened` | From the HUD. |

`effectiveness_label`: effective (corrected + back to baseline), partial, ineffective (acknowledged but nothing changed; a comprehension red flag), or ignored.

### Calibration (`backend/calibration.py`)

> Architecturally this is a **contextual bandit** (context = operator + situation, arms = threshold/channel settings, reward = effectiveness). For the demo it's implemented as a **transparent rule-based policy update**, so every change carries a human-readable reason in `calibration_history`.

Over each operator's last 12 scored tier-4 events (minimum 5):

| Condition | Policy change | Intent |
|---|---|---|
| effective ≥ 60% **and** ignored ≤ 15% | soft threshold −0.02, channel → visual | **Raise trust:** earlier, quieter nudges |
| ignored ≥ 35% **or** (reaction latency rising > 50% **and** effective < 60%) | soft threshold +0.03, channel → visual + audio | **Lower frequency, escalate channel:** speak less, but louder |
| "why" opened ≥ 50% (Loop 2) | ambient z −0.1 (min 1.5) | The one-liner isn't enough; give earlier ambient context |

The soft threshold is clamped to [0.35, 0.65] and always stays below the hard floor. **Tier 5 is never calibrated.**

Result after two weeks of history: the three low-trust dismissers end at 0.65 + audio. The five fast-confident and cautious-rechecker operators end at 0.35–0.44, visual only. The supervisor sees a plain-language comprehension signal per operator ("tuning messages out", "acknowledges but doesn't act: check understanding", "wants more context").

## 5. Gemini: three narrow agents, not one chatbot (`backend/agents.py`)

| Agent | When it's allowed | Grounding | Output bound |
|---|---|---|---|
| **In-cab explainer** | Only after "why" on a tier-4/5 event; single turn | Only that event's snapshot + SHAP contributors | Hard-capped at 2 sentences / 40 words server-side |
| **Briefing agent** | Only when the machine is stationary. The API returns **423 Locked** while operating. | Operator profile, ARC settings, task plan + trade-off, recent alerts, training gaps, hand-off notes | Conversational |
| **Supervisor query agent** | Supervisor dashboard | A **read-only SQL tool** (`mode=ro`, SELECT only). Every number comes from a query, and the SQL is shown in the UI. | ≤150 words |

All three use Google Gemini (`gemini-2.5-flash` by default; set `ARC_GEMINI_MODEL` to change it). If a response is blocked or empty, the agent falls back to its template. Each has a deterministic, DB-grounded fallback, so no API key or network is needed for the demo.

## 6. What the operator never has to think about

- Checklist says fuel 80%, sensor says 55% → tier 2 for the supervisor, not a nag in the cab.
- Idling 5 min → engine throttled down (tier 1, logged).
- Hydraulics at 88 °C → cooling fan boosted (tier 1, logged); only a sustained rise becomes a tier-4 card.
- Task overrun → foreman gets the trade-off, **with the safety cost of "go faster" computed by the risk model**; the operator hears "keep a safe pace" at most once per 3 h.
- Task queue, training and long conversations are unreachable while the machine is operating.
