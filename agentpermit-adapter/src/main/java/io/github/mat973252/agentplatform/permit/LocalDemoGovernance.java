package io.github.mat973252.agentplatform.permit;

import io.github.agentpermit4j.approval.InMemoryApprovalService;
import io.github.agentpermit4j.approval.InvocationFingerprinter;
import io.github.agentpermit4j.core.GateDecision;
import io.github.agentpermit4j.core.RiskAssessment;
import io.github.agentpermit4j.core.RiskLevel;
import io.github.agentpermit4j.execution.InMemoryResultIdempotencyGuard;
import io.github.agentpermit4j.execution.ResultDecisionPipeline;
import io.github.mat973252.agentplatform.core.ActionResult;
import java.time.Clock;
import java.time.Duration;

/** P0/P1.1 compatibility fixtures only: never used by the persistent approval path. */
public final class LocalDemoGovernance {
  private static final System.Logger LOG = System.getLogger(LocalDemoGovernance.class.getName());
  private final InMemoryResultIdempotencyGuard guard = new InMemoryResultIdempotencyGuard();

  public ActionResult restart(String operationId, String service, String approvalId, boolean approved) {
    // Reconstruct a short-lived demo approval from the Workflow's recorded decision after recovery.
    // New runs use PersistentGovernance and must never reconstruct approval from this boolean.
    var approvals = new InMemoryApprovalService(Clock.systemUTC(), () -> approvalId, new InvocationFingerprinter());
    if (approved) {
      approvals.request(AgentPermitAdapter.restartInvocation(operationId, service), Duration.ofMinutes(1));
      approvals.approve(approvalId);
    }
    var pipeline = new ResultDecisionPipeline(new ResultDecisionPipeline.Dependencies(
        AgentPermitAdapter::validate, invocation -> invocation,
        invocation -> new GateDecision(true, "LOCAL_DEMO_PRINCIPAL"),
        invocation -> new RiskAssessment(RiskLevel.HIGH, "DEMO_RESTART_REQUIRES_APPROVAL"),
        approvals, guard,
        invocation -> "SIMULATED_RESTART:" + invocation.arguments().get("service")
            + ";operationId=" + invocation.arguments().get("operationId"),
        event -> LOG.log(System.Logger.Level.DEBUG, "AgentPermit {0}: {1}",
            event.decision().outcome(), event.decision().reasonCode())));
    return new AgentPermitAdapter(pipeline).restart(operationId, service, approved ? approvalId : null);
  }
}
