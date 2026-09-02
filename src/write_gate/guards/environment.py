"""Environment policy: per-operation allow | block | approval."""

from __future__ import annotations

from write_gate.decision import RULE_ENV, RISK_CRITICAL, RISK_MEDIUM, GuardResult

NAME = "environment"


def check_environment(ctx) -> GuardResult:
    parsed = ctx.parsed
    if parsed.statement is None or parsed.error:
        return GuardResult.pass_(NAME)

    operation = parsed.operation if parsed.operation != "unknown" else "ddl"
    rule = ctx.policy.rule_for(operation)
    env = ctx.policy.environment
    evidence = {
        "environment": env,
        "operation": operation,
        "policy_rule": rule,
    }
    if rule == "allow":
        return GuardResult.pass_(NAME, evidence=evidence)
    if rule == "approval":
        return GuardResult.approval(
            NAME,
            RULE_ENV,
            f"{operation.upper()} requires approval in {env}",
            risk=RISK_MEDIUM,
            evidence=evidence,
        )
    # block
    risk = RISK_CRITICAL if operation in {"delete", "ddl", "update"} else RISK_MEDIUM
    return GuardResult.block(
        NAME,
        RULE_ENV,
        f"{operation.upper()} is blocked by policy in {env}",
        risk=risk,
        evidence=evidence,
    )
