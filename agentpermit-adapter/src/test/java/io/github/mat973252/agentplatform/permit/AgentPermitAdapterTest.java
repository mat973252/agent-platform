package io.github.mat973252.agentplatform.permit;

import static org.junit.jupiter.api.Assertions.*;

import io.github.agentpermit4j.approval.InMemoryApprovalService;
import io.github.agentpermit4j.approval.InvocationFingerprinter;
import io.github.agentpermit4j.audit.DecisionAuditEvent;
import io.github.agentpermit4j.core.*;
import io.github.agentpermit4j.execution.*;
import io.github.mat973252.agentplatform.core.ActionStatus;
import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class AgentPermitAdapterTest {
  private final InMemoryApprovalService approvals = new InMemoryApprovalService(
      Clock.systemUTC(), () -> "approval-1", new InvocationFingerprinter());
  private final InMemoryResultIdempotencyGuard guard = new InMemoryResultIdempotencyGuard();
  private final List<ToolInvocation> executed = new ArrayList<>();
  private final List<DecisionAuditEvent> audit = new ArrayList<>();

  @Test
  void allowedInvocationUsesTrustedContextAndReusesTheOperationResult() {
    var adapter = adapter(true, RiskLevel.LOW, invocation -> {
      executed.add(invocation);
      return "simulated";
    });
    var first = adapter.restart("operation-1", "orders", null);
    assertEquals(ActionStatus.EXECUTED, first.status());
    assertEquals(first, adapter.restart("operation-1", "orders", null));
    assertEquals(1, executed.size());
    var invocation = executed.getFirst();
    assertEquals("local-demo-agent", invocation.principal().id());
    assertEquals(new InvocationContext("local-demo", "development"), invocation.context());
    assertEquals("orders", invocation.resource().identifier());
    assertEquals("operation-1", invocation.arguments().get("operationId"));
    assertTrue(audit.stream().anyMatch(event -> event.decision().outcome() == DecisionOutcome.EXECUTED));
  }

  @Test
  void deniedInvocationNeverReachesTheExecutor() {
    var result = adapter(false, RiskLevel.LOW, this::execute).restart("operation-1", "orders", null);
    assertEquals(ActionStatus.DENIED, result.status());
    assertEquals("TEST_DENIED", result.reasonCode());
    assertTrue(executed.isEmpty());
  }

  @Test
  void approvalMustMatchTheInvocationBeforeExecution() {
    var adapter = adapter(true, RiskLevel.HIGH, this::execute);
    assertEquals(ActionStatus.APPROVAL_REQUIRED, adapter.restart("operation-1", "orders", null).status());
    var invocation = AgentPermitAdapter.restartInvocation("operation-1", "orders");
    var approval = approvals.request(invocation, Duration.ofMinutes(1));
    assertEquals(ActionStatus.APPROVAL_REQUIRED, adapter.restart("operation-1", "orders", approval.id()).status());
    assertTrue(executed.isEmpty());
    approvals.approve(approval.id());
    assertEquals("APPROVAL_INVOCATION_MISMATCH",
        adapter.restart("operation-2", "orders", approval.id()).reasonCode());
    assertTrue(executed.isEmpty());
    assertEquals(ActionStatus.EXECUTED, adapter.restart("operation-1", "orders", approval.id()).status());
    assertEquals(ActionStatus.EXECUTED, adapter.restart("operation-1", "orders", approval.id()).status());
    assertEquals(1, executed.size());
  }

  @Test
  void executionFailureIsExplicitAndDoesNotRetryTheSideEffectInMemory() {
    var adapter = adapter(true, RiskLevel.LOW, invocation -> {
      executed.add(invocation);
      throw new IllegalStateException("synthetic failure");
    });
    var failure = adapter.restart("operation-1", "orders", null);
    assertEquals(ActionStatus.FAILED, failure.status());
    assertEquals("EXECUTION_FAILED", failure.reasonCode());
    assertNull(failure.output());
    assertEquals(failure, adapter.restart("operation-1", "orders", null));
    assertEquals(1, executed.size());
  }

  @Test
  void unsupportedResourcesFailBeforeExecution() {
    var result = adapter(true, RiskLevel.LOW, this::execute).restart("operation-1", "payments", null);
    assertEquals(ActionStatus.DENIED, result.status());
    assertTrue(executed.isEmpty());
  }

  @ParameterizedTest
  @NullAndEmptySource
  @ValueSource(strings = {" "})
  void aMissingOperationIdCannotBypassIdempotency(String operationId) {
    var adapter = adapter(true, RiskLevel.LOW, this::execute);
    assertThrows(IllegalArgumentException.class, () -> adapter.restart(operationId, "orders", null));
    assertTrue(executed.isEmpty());
  }

  private String execute(ToolInvocation invocation) {
    executed.add(invocation);
    return "simulated";
  }

  private AgentPermitAdapter adapter(boolean permitted, RiskLevel risk, ResultToolExecutor executor) {
    return new AgentPermitAdapter(new ResultDecisionPipeline(new ResultDecisionPipeline.Dependencies(
        AgentPermitAdapter::validate, invocation -> invocation,
        invocation -> new GateDecision(permitted, permitted ? "TEST_ALLOWED" : "TEST_DENIED"),
        invocation -> new RiskAssessment(risk, "TEST_RISK"), approvals, guard, executor, audit::add)));
  }
}
