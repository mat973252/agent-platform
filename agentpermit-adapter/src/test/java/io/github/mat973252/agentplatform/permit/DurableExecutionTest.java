package io.github.mat973252.agentplatform.permit;

import static org.junit.jupiter.api.Assertions.*;

import io.github.mat973252.agentplatform.core.ActionStatus;
import io.github.mat973252.agentplatform.core.ApprovalDecision;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import io.github.agentpermit4j.approval.InvocationFingerprinter;
import io.github.agentpermit4j.core.DecisionOutcome;
import io.github.agentpermit4j.core.DecisionResult;
import io.github.agentpermit4j.execution.ToolExecutionResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

class DurableExecutionTest {
  private final Instant now = Instant.parse("2026-09-08T00:00:00Z");
  private final Clock clock = Clock.fixed(now, ZoneOffset.UTC);
  private SingleConnectionDataSource source;
  private JdbcApprovalStore approvals;
  private JdbcExecutionStore executions;
  private JdbcRestartLedger ledger;

  @BeforeEach
  void setUp() {
    source = new SingleConnectionDataSource("jdbc:h2:mem:" + UUID.randomUUID(), "sa", "", true);
    new ResourceDatabasePopulator(new ClassPathResource("db/migration/V1__approvals.sql"),
        new ClassPathResource("db/migration/V2__durable_execution.sql")).execute(source);
    approvals = new JdbcApprovalStore(source, clock);
    executions = new JdbcExecutionStore(source, clock);
    ledger = new JdbcRestartLedger(source, clock);
    var policy = new PersistentGovernance(approvals, "approver", true);
    policy.prepare("run-1", "run-1:restart:1", "orders", "approval-1", now.plusSeconds(30).toEpochMilli());
    approvals.decide("run-1", "approval-1", ApprovalDecision.APPROVE, "approver");
  }

  @AfterEach
  void close() { source.destroy(); }

  private DurableGovernance worker(DemoFailureMode failure) {
    return new DurableGovernance(new PersistentGovernance(approvals, "approver", true),
        new JdbcExecutionStore(source, clock), new JdbcRestartLedger(source, clock), failure);
  }

  @Test
  void aNewWorkerReusesTheDurableResultWithoutAnotherLedgerMutation() {
    var first = worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1");
    assertEquals(ActionStatus.EXECUTED, first.status());
    assertEquals(first, worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1"));
    assertEquals(1, ledger.generation("orders"));
    assertTrue(approvals.get("approval-1").executionStarted());
  }

  @Test
  void committedSideEffectWithLostResponseIsReconciledFromTheLedger() {
    var uncertain = worker(DemoFailureMode.FAIL_AFTER_LEDGER_COMMIT).restart("run-1:restart:1", "orders", "approval-1");
    assertEquals(ActionStatus.RECONCILIATION_REQUIRED, uncertain.status());
    assertEquals(1, ledger.generation("orders"));
    var recovered = worker(DemoFailureMode.NONE).reconcile("run-1:restart:1", "orders", "approval-1");
    assertEquals(ActionStatus.EXECUTED, recovered.status());
    assertEquals(recovered, worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1"));
    assertEquals(1, ledger.generation("orders"));
  }

  @Test
  void missingReceiptRemainsUnknownAndCannotBeBlindlyRetried() {
    assertEquals(ActionStatus.RECONCILIATION_REQUIRED,
        worker(DemoFailureMode.FAIL_BEFORE_LEDGER_WRITE).restart("run-1:restart:1", "orders", "approval-1").status());
    assertEquals(ActionStatus.RECONCILIATION_REQUIRED,
        worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1").status());
    assertEquals(ActionStatus.RECONCILIATION_REQUIRED,
        worker(DemoFailureMode.NONE).reconcile("run-1:restart:1", "orders", "approval-1").status());
    executions.closeUnknown("run-1:restart:1", "operator", "Unable to confirm downstream outcome");
    assertEquals(ActionStatus.CLOSED_UNKNOWN,
        worker(DemoFailureMode.NONE).reconcile("run-1:restart:1", "orders", "approval-1").status());
    assertEquals(0, ledger.generation("orders"));
    assertEquals("operator", executions.get("run-1:restart:1").closedBy());
  }

  @Test
  void manualClosureSurvivesLateCompletionAndLedgerConfirmation() {
    worker(DemoFailureMode.FAIL_AFTER_LEDGER_COMMIT).restart("run-1:restart:1", "orders", "approval-1");
    executions.closeUnknown("run-1:restart:1", "operator", "Outcome left unresolved");
    var receipt = ledger.find("run-1:restart:1");
    executions.complete("run-1:restart:1", new ToolExecutionResult(
        new DecisionResult(DecisionOutcome.EXECUTED, "LATE_RESULT"), receipt.output()));
    assertEquals(ActionStatus.CLOSED_UNKNOWN,
        worker(DemoFailureMode.NONE).reconcile("run-1:restart:1", "orders", "approval-1").status());
    assertNull(executions.get("run-1:restart:1").output());
    assertEquals("operator", executions.get("run-1:restart:1").closedBy());
    assertEquals(1, ledger.generation("orders"));
  }

  @Test
  void reconciliationBeforeAnExecutionRecordExistsFencesLateExecution() {
    assertEquals(ActionStatus.RECONCILIATION_REQUIRED,
        worker(DemoFailureMode.NONE).reconcile("run-1:restart:1", "orders", "approval-1").status());
    assertEquals(ActionStatus.RECONCILIATION_REQUIRED,
        worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1").status());
    assertEquals(0, ledger.generation("orders"));
    assertFalse(approvals.get("approval-1").executionStarted());
  }

  @Test
  void acknowledgingAnOlderCheckDoesNotHideAManualClosure() {
    worker(DemoFailureMode.FAIL_BEFORE_LEDGER_WRITE).restart("run-1:restart:1", "orders", "approval-1");
    executions.requestCheck("run-1:restart:1");
    long oldVersion = executions.undelivered().getFirst().reconciliationVersion();
    executions.closeUnknown("run-1:restart:1", "operator", "Outcome left unresolved");
    executions.delivered("run-1:restart:1", oldVersion);
    assertEquals(1, executions.undelivered().size());
    executions.delivered("run-1:restart:1", oldVersion + 1);
    assertTrue(executions.undelivered().isEmpty());
  }

  @Test
  void changedInvocationCannotReadAnotherResultOrExecuteAgain() {
    worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1");
    var changed = worker(DemoFailureMode.NONE).restart("run-1:restart:1", "payments", "approval-1");
    assertEquals(ActionStatus.DENIED, changed.status());
    assertNull(changed.output());
    assertEquals(1, ledger.generation("orders"));
  }

  @Test
  void confirmedResultRecoveryDoesNotRequireNewExecutionPermission() {
    var expected = worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1");
    var expired = new JdbcApprovalStore(source, Clock.fixed(now.plusSeconds(60), ZoneOffset.UTC));
    var recovered = new DurableGovernance(new PersistentGovernance(expired, "new-approver", false),
        executions, ledger, DemoFailureMode.NONE);
    assertEquals(expected, recovered.restart("run-1:restart:1", "orders", "approval-1"));
    assertEquals(1, ledger.generation("orders"));
  }

  @Test
  void independentWorkersNeverTakeOverAnInProgressExecution() throws Exception {
    var connection1 = new DriverManagerDataSource(source.getUrl(), "sa", "");
    var connection2 = new DriverManagerDataSource(source.getUrl(), "sa", "");
    var firstGuard = new JdbcResultIdempotencyGuard(new JdbcExecutionStore(connection1, clock),
        new PersistentGovernance(new JdbcApprovalStore(connection1, clock), "approver", true));
    var secondGuard = new JdbcResultIdempotencyGuard(new JdbcExecutionStore(connection2, clock),
        new PersistentGovernance(new JdbcApprovalStore(connection2, clock), "approver", true));
    var downstream = new JdbcRestartLedger(connection1, clock);
    var invocation = AgentPermitAdapter.restartInvocation("run-1:restart:1", "orders");
    String fingerprint = new InvocationFingerprinter().fingerprint(invocation).value();
    var committed = new CountDownLatch(1);
    var release = new CountDownLatch(1);
    try (var threads = Executors.newVirtualThreadPerTaskExecutor()) {
      var first = threads.submit(() -> firstGuard.executeOnce("run-1:restart:1", invocation, "approval-1", () -> {
        var receipt = downstream.restart("run-1:restart:1", fingerprint, "orders");
        committed.countDown();
        try { release.await(); }
        catch (InterruptedException interrupted) { throw new IllegalStateException(interrupted); }
        return new ToolExecutionResult(new DecisionResult(DecisionOutcome.EXECUTED, "DEMO_RESTART_REQUIRES_APPROVAL"), receipt.output());
      }));
      try {
        assertTrue(committed.await(5, TimeUnit.SECONDS));
        var duplicate = secondGuard.executeOnce("run-1:restart:1", invocation, "approval-1", () -> {
          throw new AssertionError("Another worker must not invoke the executor");
        });
        assertEquals(DecisionOutcome.FAILED, duplicate.decision().outcome());
        assertEquals("EXECUTION_IN_PROGRESS", duplicate.decision().reasonCode());
      } finally { release.countDown(); }
      assertEquals(DecisionOutcome.EXECUTED, first.get(5, TimeUnit.SECONDS).decision().outcome());
    }
    assertEquals(1, ledger.generation("orders"));
    assertEquals(ActionStatus.EXECUTED, worker(DemoFailureMode.NONE).restart("run-1:restart:1", "orders", "approval-1").status());
  }

  @Test
  void theDownstreamDeduplicatesConcurrentRequestsIndependentlyOfTheGuard() throws Exception {
    var independent = new JdbcRestartLedger(new DriverManagerDataSource(source.getUrl(), "sa", ""), clock);
    String fingerprint = new InvocationFingerprinter().fingerprint(AgentPermitAdapter.restartInvocation("run-1:restart:1", "orders")).value();
    try (var threads = Executors.newVirtualThreadPerTaskExecutor()) {
      var one = threads.submit(() -> independent.restart("run-1:restart:1", fingerprint, "orders"));
      var two = threads.submit(() -> independent.restart("run-1:restart:1", fingerprint, "orders"));
      assertEquals(one.get(5, TimeUnit.SECONDS), two.get(5, TimeUnit.SECONDS));
    }
    assertEquals(1, ledger.generation("orders"));
  }
}
