# Subagent Transport Protocol

This file owns parent-side transport and recovery for workflows that keep one or more active subagents. Skill-local files own task payloads, role routing, result schemas, validation gates, apply gates, and workflow-level retry or failure policy.

## Governing Rules
- Wait poll timeout is `300000` milliseconds.
- Every parent-to-subagent task MUST be idempotent or restart-resumable.
- `agent-workflows` plugin support owner `lib/subagent-transport/protocol.md` owns parent-side subagent transport, liveness tracking, and recovery mechanics.
- Subagents that are no longer current and will not receive corrective feedback or follow-up work MUST be closed.
- `direct_agent` means one independently tracked current subagent whose identifier is held in parent runtime state.
- `agent_pool` means a registry-backed workflow that owns one run-local `agent.json`; it may currently contain one or more active subagents.
- A locally executed role that does not create a subagent has no subagent transport mode.
- Each subagent-using workflow MUST state its selected transport mode before sending work.

## Direct Agent Mode
- Parent workflow keeps the current `agent_id` in its own runtime state.
- `agent.json` MUST NOT be created or read for `direct_agent` mode.
- Use this tracker command for timeout-triggered or terminal-without-result recovery:

```bash
python <resolved-agent-workflows-plugin-root>/lib/subagent-transport/tool/subagent_track.py --agent-id=<agent_id>
```

## Agent Pool Mode
- Parent workflow creates one run-local `agent.json` before assigning pooled work.
- `agent.json` is a flat JSON object whose keys are current active `agent_id` values.
- `agent.json` values are workflow-local metadata and MUST NOT be interpreted by this transport protocol.
- Parent workflow MUST update `agent.json` when one pooled agent is created, replaced, removed, or no longer current.
- Use this tracker command for timeout-triggered or terminal-without-result recovery of one pooled agent:

```bash
python <resolved-agent-workflows-plugin-root>/lib/subagent-transport/tool/subagent_track.py --agent-id=<agent_id>
```

## Agent Creation
- This protocol does not restrict agent creation shape.
- Parent workflows create subagents through the selected harness adapter without inherited surrounding conversation context and supply the complete workflow-owned role contract in the task prompt.
- A harness adapter MUST express that context boundary with the harness's current supported creation field; canonical plugin contracts MUST NOT expose a harness-specific or obsolete field name.
- This protocol receives only the created `agent_id`.
- Selection of role prompt, task payload, and replacement prompt belongs to the workflow owner; inherited surrounding conversation context remains forbidden.
- Consumer-local named-agent TOML is not a launch dependency or fallback.

## Agent Lifecycle
- Parent workflows MUST close one subagent after its result has been handled when the workflow will not send corrective feedback or follow-up work to that same `agent_id`.
- Parent workflows MUST close stale, replaced, or superseded subagents whenever the runtime still allows closing them.

## Recovery
- If `wait_agent` returns the current task result, handle that result under the workflow contract.
- If `wait_agent` times out, run the tracker command for the current `agent_id`.
- If `wait_agent` reports that the current agent closed or completed without a usable current result, run the tracker command for the current `agent_id` before replacing the agent.
- If tracker output is `OK`, keep the same current `agent_id`; continue waiting or send workflow-owned corrective feedback to that same agent.
- If tracker output is `TIMEOUT`, close the current agent, create one replacement agent through the workflow-owned agent creation profile, bind the replacement as current, and resend the same current outbound request.
- If tracker output is `ERROR_AGENT_ID_NOT_FOUND`, treat the current `agent_id` binding as stale or invalid: find the correct current `agent_id` from the workflow's own spawn, wait, or pool-binding state and continue recovery for that `agent_id`; if no correct current `agent_id` can be identified, create one new agent through the workflow-owned agent creation profile, bind it as current, and resend the same current outbound request.
- `wait_agent` timeout, `wait_agent` terminal state without the current result, missing result artifact, malformed result, failed validation, parent quality concerns, slow progress, scope change, or instruction correction MUST NOT by themselves justify replacing the current agent while tracker output is `OK`.
