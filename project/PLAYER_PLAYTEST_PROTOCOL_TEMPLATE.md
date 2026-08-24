# Qinghuai player playtest protocol (template)

Status: blank protocol for real human playtests. It is not a simulation result and must not be filled with automated-agent runs.

## Scope and consent

This session evaluates whether a person can understand and play the public chat-world loop. Participation is voluntary. The facilitator must explain the session, answer procedural questions, and stop immediately on request. Do not collect names, contact details, account identifiers, private prompts, private Memory, credentials, or raw chat logs.

Assign a random participant code such as `P-001`; keep the code-to-person mapping outside the repository. Record only the anonymized fields in `PLAYER_PLAYTEST_ANONYMOUS_RECORD_TEMPLATE.json`.

## Fixed session setup

- Build/commit under test: `________________`
- Session date (local): `________________`
- Facilitator code: `________________`
- Participant code: `________________`
- Route or scenario label shown to the participant: `________________`
- Device/input notes that could affect the session: `________________`

The participant receives only the public interface and public scenario context. Do not reveal NPC private stance, Goal, Memory, hidden events, seed, or the fixed `observer`/`pro_lin`/`pro_zhao` strategy text.

## Procedure

1. Give the participant the short public premise and ask them to think aloud only if they are comfortable. Do not coach toward a branch.
2. Start the session and record the start/end timestamps in the anonymous record.
3. Ask the participant to explore the world, use invitations/chat when they choose, and attempt the visible task. The facilitator may clarify controls, but must not suggest NPC private state or a winning action.
4. At Day7 or an early stop, ask the neutral questions below. Record paraphrased observations and event counts, not conversation text.
5. Mark whether the participant consented to anonymous aggregate use, then remove any scratch notes that contain identifying information.

## Neutral post-session questions

- What did you think the visible objective was?
- Which public information did you use to decide what to do?
- Could you tell whether an NPC accepted, refused, or needed conditions?
- Did the day/time and ending state make sense?
- Where did you feel blocked or unsure?
- Did any screen expose information you did not expect to see?
- What one change would make the next attempt clearer?

## Facilitator boundaries

- Do not replay a failed session with a replacement seed and call it the same participant attempt.
- Do not edit stance, Goal, authorization, branch, or event state for convenience.
- If the backend, provider, browser, or database fails, record an infrastructure stop and keep it separate from gameplay success.
- If a participant gives free-form personal information, omit it from the record and destroy the raw note.
- A human playtest is qualitative usability evidence; it is not a substitute for the preregistered 15-run ITT matrix.

## Anonymous record checklist

- [ ] Random participant code only.
- [ ] No raw dialogue, prompt, private Memory, credential, or absolute local path.
- [ ] Player speech count is a count, not message contents.
- [ ] Completion/early-stop reason is recorded without personal explanation.
- [ ] Infrastructure failures are separated from gameplay observations.
- [ ] Consent and withdrawal state are recorded.
