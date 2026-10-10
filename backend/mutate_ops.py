import subprocess
import sys

S = "app/services/admin.py"
F = "app/services/flags.py"
J = "app/services/jobs.py"
N = "app/services/notifications.py"
I = "app/services/integrations.py"
SC = "app/services/scheduler.py"
M = [
    (F, "return bool((flag.org_overrides or {}).get(str(organization_id), flag.enabled))", "return bool(flag.enabled)"),
    (F, "return flag is not None and resolve(flag, organization_id)", "return flag is None or resolve(flag, organization_id)"),
    (S, "if db.scalars(select(FeatureFlag.id).where(FeatureFlag.key == key)).first():", "if False:"),
    (S, "flag.org_overrides = {**flag.org_overrides, str(organization_id): enabled}", "flag.org_overrides = {str(organization_id): enabled}"),
    (S, "if str(organization_id) not in flag.org_overrides:", "if False:"),
    (S, "flag.org_overrides = {k: v for k, v in flag.org_overrides.items() if k != str(organization_id)}", "flag.org_overrides = {}"),
    (S, "if changes.get(\"enabled\") is not None and changes[\"enabled\"] != flag.enabled:", "if changes.get(\"enabled\") is not None:"),
    (S, "case.resolved_at = utcnow() if status == \"resolved\" else None", "case.resolved_at = utcnow()"),
    (S, "if target is None or not _is_staff(db, target.id):", "if target is None:"),
    (S, "if changes.get(\"unassign\") and case.assigned_to_user_id is not None:", "if False:"),
    (S, "if target.id != case.assigned_to_user_id:", "if True:"),
    (S, "if changes.get(\"priority\") is not None and changes[\"priority\"] != case.priority:", "if changes.get(\"priority\") is not None:"),
    (S, "case.updated_at = utcnow()", "pass"),
    (S, "requester_email.strip().lower() if requester_email else None", "requester_email if requester_email else None"),
    (S, "case((SupportCase.status == \"resolved\", 1), else_=0), SupportCase.created_at.desc()", "SupportCase.created_at.desc()"),
    (S, "where.append(SupportCase.assigned_to_user_id == assigned_to)", "pass"),
    (S, "where.append(SupportCase.organization_id == organization_id)", "pass"),
    (S, "if organization_id is None and user_id is None:", "if False:"),
    (S, "if event.resolved_at is not None:", "if False:"),
    (S, "stmt = stmt.where(SystemEvent.resolved_at.is_(None))", "pass"),
    (S, "stmt = stmt.where(SystemEvent.severity == severity)", "pass"),
    (S, "stmt = stmt.where(SystemEvent.kind == kind)", "pass"),
    (S, "items[-1].id if len(rows) > limit else None", "items[-1].id if len(rows) >= limit else None"),
    (S, "Job.heartbeat_at < now - STUCK_AFTER", "Job.heartbeat_at < now"),
    (S, "Job.status == \"failed\", Job.finished_at >= day_ago", "Job.status == \"failed\""),
    (S, "Notification.email_status == \"pending\", Notification.email_after <= now", "Notification.email_status == \"pending\""),
    (S, "Notification.email_status == \"failed\", Notification.email_sent_at >= day_ago", "Notification.email_status == \"failed\""),
    (S, "Job.status == \"queued\", Job.run_after <= now", "Job.status == \"queued\""),
    (S, "status=\"ok\" if database_ok and errors == 0 and stuck == 0 else \"attention\"", "status=\"ok\""),
    (S, "SystemEvent.severity == \"warning\", SystemEvent.resolved_at.is_(None)", "SystemEvent.severity == \"warning\""),
    (S, "SupportCase.status != \"resolved\")) or 0\n    return HealthOut", "True)) or 0\n    return HealthOut"),
    (J, "system_events.record(", "(lambda *a, **k: None)("),
    (N, "system_events.record(", "(lambda *a, **k: None)("),
    (I, "if integration.consecutive_failures == FAILING_AFTER and not needs_reauth:", "if integration.consecutive_failures >= FAILING_AFTER and not needs_reauth:"),
    (I, "FAILING_AFTER = 3", "FAILING_AFTER = 2"),
    (SC, "system_events.record(", "(lambda *a, **k: None)("),
]
survivors = []
for n, (path, old, new) in enumerate(M, 1):
    src = open(path, encoding="utf-8").read()
    if old not in src:
        print(f"{n}: NO MATCH in {path}: {old[:50]!r}".encode("ascii", "replace").decode(), flush=True)
        continue
    open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_admin_ops.py", "-x", "-q", "--no-header", "-p", "no:warnings"], capture_output=True, timeout=900)
    finally:
        open(path, "w", encoding="utf-8").write(src)
    state = "killed" if r.returncode == 1 else f"SURVIVED(rc={r.returncode})"
    if state != "killed":
        survivors.append((n, path, old[:60]))
    print(f"{n}: {state} {path} {old[:50]!r}".encode("ascii", "replace").decode(), flush=True)
print("SURVIVORS:", survivors, flush=True)
print("DONE", flush=True)
