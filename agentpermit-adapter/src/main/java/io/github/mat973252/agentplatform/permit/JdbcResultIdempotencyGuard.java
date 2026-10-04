package io.github.mat973252.agentplatform.permit;

import io.github.agentpermit4j.approval.InvocationFingerprinter;
import io.github.agentpermit4j.core.DecisionOutcome;
import io.github.agentpermit4j.core.DecisionResult;
import io.github.agentpermit4j.core.ToolInvocation;
import io.github.agentpermit4j.execution.ResultIdempotencyGuard;
import io.github.agentpermit4j.execution.ToolExecutionResult;
import java.util.function.Supplier;

/** One persistent owner per logical operation; incomplete owners are never replaced. */
final class JdbcResultIdempotencyGuard implements ResultIdempotencyGuard {
  private final JdbcExecutionStore executions;
  private final PersistentGovernance policy;
  private final InvocationFingerprinter fingerprinter = new InvocationFingerprinter();

  JdbcResultIdempotencyGuard(JdbcExecutionStore executions, PersistentGovernance policy) {
    this.executions = executions;
    this.policy = policy;
  }

  @Override
  public ToolExecutionResult executeOnce(String key, ToolInvocation invocation, Supplier<ToolExecutionResult> execute) {
    return denied("APPROVAL_REQUIRED_FOR_LEDGER");
  }

  @Override
  public ToolExecutionResult executeOnce(String key, ToolInvocation invocation, String approvalId, Supplier<ToolExecutionResult> execute) {
    if (!key.equals(invocation.arguments().get("operationId"))) return denied("OPERATION_ID_MISMATCH");
    String fingerprint = fingerprinter.fingerprint(invocation).value();
    boolean owner;
    try {
      owner = executions.acquire(key, approvalId, fingerprint, () -> policy.consume(approvalId, fingerprint));
    } catch (IllegalStateException rejected) {
      return denied("APPROVAL_CONSUMPTION_REJECTED");
    }
    var record = executions.find(key);
    if (record == null || !record.approvalId().equals(approvalId) || !record.fingerprint().equals(fingerprint)) {
      return denied("EXECUTION_BINDING_MISMATCH");
    }
    if (owner) record = executions.complete(key, execute.get());
    var outcome = record.status().equals("SUCCEEDED") ? DecisionOutcome.EXECUTED : DecisionOutcome.FAILED;
    return new ToolExecutionResult(new DecisionResult(outcome, record.reasonCode()), record.output());
  }

  private ToolExecutionResult denied(String reason) {
    return new ToolExecutionResult(new DecisionResult(DecisionOutcome.DENIED, reason), null);
  }
}
