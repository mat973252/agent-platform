package io.github.mat973252.agentplatform.permit;

import io.github.agentpermit4j.approval.InvocationFingerprinter;
import io.github.mat973252.agentplatform.core.ActionResult;
import io.github.mat973252.agentplatform.core.ActionStatus;

public final class DurableGovernance {
  private final PersistentGovernance policy;
  private final JdbcExecutionStore executions;
  private final JdbcRestartLedger ledger;
  private final DemoFailureMode failure;
  private final InvocationFingerprinter fingerprinter = new InvocationFingerprinter();

  public DurableGovernance(PersistentGovernance policy, JdbcExecutionStore executions,
      JdbcRestartLedger ledger, DemoFailureMode failure) {
    this.policy = policy;
    this.executions = executions;
    this.ledger = ledger;
    this.failure = failure;
  }

  public ActionResult restart(String operationId, String service, String approvalId) {
    String fingerprint = fingerprint(operationId, service);
    var existing = executions.find(operationId);
    // Reading a prior operation's outcome is not authorization to invoke its tool again.
    if (existing != null) return boundResult(existing, approvalId, fingerprint);
    var pipeline = policy.pipeline(new JdbcResultIdempotencyGuard(executions, policy), invocation -> {
      if (failure == DemoFailureMode.FAIL_BEFORE_LEDGER_WRITE) throw new IllegalStateException("Synthetic pre-write failure");
      var receipt = ledger.restart(operationId, fingerprint, service);
      afterCommit();
      return receipt.output();
    });
    var result = new AgentPermitAdapter(pipeline).restart(operationId, service, approvalId);
    existing = executions.find(operationId);
    return existing == null ? result : boundResult(existing, approvalId, fingerprint);
  }

  public ActionResult reconcile(String operationId, String service, String approvalId) {
    String fingerprint = fingerprint(operationId, service);
    if (!policy.matchesApproval(approvalId, operationId, fingerprint)) return mismatch();
    var record = executions.freezeUnknown(operationId, approvalId, fingerprint);
    var result = boundResult(record, approvalId, fingerprint);
    if (result.status() != ActionStatus.RECONCILIATION_REQUIRED) return result;
    var receipt = ledger.find(operationId);
    if (receipt == null) return result;
    if (!receipt.fingerprint().equals(fingerprint) || !receipt.service().equals(service)) return mismatch();
    return executions.confirm(operationId, receipt.output(), "DEMO_RESTART_REQUIRES_APPROVAL").result();
  }

  public OperationRecord fenceUnknown(String operationId, String service, String approvalId) {
    String fingerprint = fingerprint(operationId, service);
    if (!policy.matchesApproval(approvalId, operationId, fingerprint)) throw new IllegalStateException("Execution binding mismatch");
    return executions.freezeUnknown(operationId, approvalId, fingerprint);
  }

  private ActionResult boundResult(OperationRecord record, String approvalId, String fingerprint) {
    return record.approvalId().equals(approvalId) && record.fingerprint().equals(fingerprint) ? record.result() : mismatch();
  }

  private ActionResult mismatch() { return new ActionResult(ActionStatus.DENIED, "EXECUTION_BINDING_MISMATCH", null); }

  private String fingerprint(String operationId, String service) {
    return fingerprinter.fingerprint(AgentPermitAdapter.restartInvocation(operationId, service)).value();
  }

  private void afterCommit() {
    if (failure == DemoFailureMode.FAIL_AFTER_LEDGER_COMMIT) throw new IllegalStateException("Synthetic response loss");
    if (failure == DemoFailureMode.PAUSE_AFTER_LEDGER_COMMIT) {
      try { Thread.sleep(300_000); }
      catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); throw new IllegalStateException("Synthetic pause interrupted"); }
    }
  }
}
