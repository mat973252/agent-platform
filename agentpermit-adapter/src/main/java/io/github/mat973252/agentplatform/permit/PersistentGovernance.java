package io.github.mat973252.agentplatform.permit;

import io.github.agentpermit4j.approval.InvocationFingerprinter;
import io.github.agentpermit4j.core.GateDecision;
import io.github.agentpermit4j.core.RiskAssessment;
import io.github.agentpermit4j.core.RiskLevel;
import io.github.agentpermit4j.execution.InMemoryResultIdempotencyGuard;
import io.github.agentpermit4j.execution.ResultDecisionPipeline;
import io.github.agentpermit4j.execution.ResultIdempotencyGuard;
import io.github.agentpermit4j.execution.ResultToolExecutor;
import io.github.mat973252.agentplatform.core.ActionResult;
import io.github.mat973252.agentplatform.core.ActionStatus;

/** Durable approval verification for the fixed local tool; the tool itself is still simulated. */
public final class PersistentGovernance {
  private static final System.Logger LOG = System.getLogger(PersistentGovernance.class.getName());
  private final JdbcApprovalStore store;
  private final String approver;
  private final boolean restartEnabled;
  private final InvocationFingerprinter fingerprinter = new InvocationFingerprinter();
  private final InMemoryResultIdempotencyGuard guard = new InMemoryResultIdempotencyGuard();

  public PersistentGovernance(JdbcApprovalStore store, String approver, boolean restartEnabled) {
    this.store = store;
    this.approver = approver;
    this.restartEnabled = restartEnabled;
  }

  public ActionResult prepare(String runId, String operationId, String service, String approvalId, long expiresAt) {
    if (!operationId.equals(runId + ":restart:1")) throw new IllegalStateException("Operation does not belong to Run");
    var invocation = AgentPermitAdapter.restartInvocation(operationId, service);
    if (!restartEnabled || !AgentPermitAdapter.validate(invocation).permitted()) {
      return new ActionResult(ActionStatus.DENIED, "RESTART_DISABLED", null);
    }
    store.create(approvalId, runId, operationId, service, fingerprinter.fingerprint(invocation).value(), expiresAt);
    return new ActionResult(ActionStatus.APPROVAL_REQUIRED, "DEMO_RESTART_REQUIRES_APPROVAL", null);
  }

  public ActionResult restart(String operationId, String service, String approvalId) {
    var pipeline = pipeline(guard, invocation -> {
      store.claim(approvalId, fingerprinter.fingerprint(invocation).value(), approver);
      return "SIMULATED_RESTART:" + service + ";operationId=" + operationId;
    });
    return new AgentPermitAdapter(pipeline).restart(operationId, service, approvalId);
  }

  ResultDecisionPipeline pipeline(ResultIdempotencyGuard idempotency, ResultToolExecutor executor) {
    return new ResultDecisionPipeline(new ResultDecisionPipeline.Dependencies(
        AgentPermitAdapter::validate, invocation -> invocation,
        invocation -> new GateDecision(restartEnabled, restartEnabled ? "LOCAL_SERVICE_ALLOWED" : "RESTART_DISABLED"),
        invocation -> new RiskAssessment(RiskLevel.HIGH, "DEMO_RESTART_REQUIRES_APPROVAL"),
        (id, invocation) -> verify(id, fingerprinter.fingerprint(invocation).value()),
        idempotency, executor, event -> LOG.log(System.Logger.Level.DEBUG, "AgentPermit {0}: {1}",
            event.decision().outcome(), event.decision().reasonCode())));
  }

  void consume(String approvalId, String fingerprint) { store.consume(approvalId, fingerprint, approver); }

  boolean matchesApproval(String approvalId, String operationId, String fingerprint) {
    var record = store.get(approvalId);
    return record.operationId().equals(operationId) && record.fingerprint().equals(fingerprint);
  }

  public boolean canApprove(String actor, String service) {
    return restartEnabled && approver.equals(actor) && "orders".equals(service);
  }

  private GateDecision verify(String id, String fingerprint) {
    var record = store.get(id);
    if (!record.fingerprint().equals(fingerprint)) return new GateDecision(false, "APPROVAL_INVOCATION_MISMATCH");
    if (!record.status().equals("APPROVE")) return new GateDecision(false, "APPROVAL_" + record.status());
    if (!approver.equals(record.decidedBy())) return new GateDecision(false, "APPROVER_PERMISSION_REVOKED");
    return store.valid(record, fingerprint, approver)
        ? new GateDecision(true, "APPROVAL_VALID") : new GateDecision(false, "APPROVAL_EXPIRED");
  }
}
