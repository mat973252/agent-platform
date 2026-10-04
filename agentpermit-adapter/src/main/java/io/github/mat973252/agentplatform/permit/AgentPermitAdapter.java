package io.github.mat973252.agentplatform.permit;

import io.github.agentpermit4j.core.*;
import io.github.agentpermit4j.execution.ResultDecisionPipeline;
import io.github.mat973252.agentplatform.core.ActionResult;
import io.github.mat973252.agentplatform.core.ActionStatus;
import java.util.Map;
import java.util.Objects;

/** Builds trusted server-side invocations and maps AgentPermit decisions into runtime data. */
public final class AgentPermitAdapter {
  private final ResultDecisionPipeline pipeline;

  public AgentPermitAdapter(ResultDecisionPipeline pipeline) {
    this.pipeline = Objects.requireNonNull(pipeline, "pipeline");
  }

  public ActionResult restart(String operationId, String service, String approvalId) {
    var result = pipeline.process(restartInvocation(operationId, service), approvalId, operationId);
    var status = switch (result.decision().outcome()) {
      case EXECUTED -> ActionStatus.EXECUTED;
      case APPROVAL_REQUIRED -> ActionStatus.APPROVAL_REQUIRED;
      case DENIED -> ActionStatus.DENIED;
      case FAILED -> ActionStatus.FAILED;
    };
    return new ActionResult(status, result.decision().reasonCode(), result.output());
  }

  static ToolInvocation restartInvocation(String operationId, String service) {
    if (operationId == null || operationId.isBlank()) {
      throw new IllegalArgumentException("operationId is required");
    }
    return new ToolInvocation(
        new ToolDescriptor("ops.restart", ToolEffect.WRITE, Reversibility.IRREVERSIBLE, DataSensitivity.INTERNAL),
        new Principal("local-demo-agent", Map.of("role", "diagnostics")),
        new Action("restart"), new Resource("service", service, Map.of()),
        new InvocationContext("local-demo", "development"),
        Map.of("service", service, "operationId", operationId));
  }

  static GateDecision validate(ToolInvocation invocation) {
    boolean supported = "orders".equals(invocation.resource().identifier());
    return new GateDecision(supported, supported ? "INVOCATION_VALID" : "SERVICE_NOT_ALLOWED");
  }
}
