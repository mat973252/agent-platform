package io.github.mat973252.agentplatform.permit;

import static org.junit.jupiter.api.Assertions.*;

import io.github.agentpermit4j.approval.InvocationFingerprinter;
import io.github.mat973252.agentplatform.core.ActionStatus;
import io.github.mat973252.agentplatform.core.ApprovalDecision;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

class AgentPermitCompatibilityTest {
  // Captured with the previously pinned 0.2.0 commit c33911c595e5718b144d2bdb939bb3e23e17c909.
  private static final String LEGACY_FINGERPRINT = "ea74ffb575f1e70216b968fb5dcece2d78d66a8cb9f9b58ebdddb76ab903a2e7";
  private final Clock clock = Clock.fixed(Instant.parse("2026-09-08T00:00:00Z"), ZoneOffset.UTC);
  private SingleConnectionDataSource source;
  private JdbcRestartLedger ledger;
  private DurableGovernance worker;

  @BeforeEach
  void loadLegacyApproval() {
    source = new SingleConnectionDataSource("jdbc:h2:mem:" + UUID.randomUUID(), "sa", "", true);
    new ResourceDatabasePopulator(new ClassPathResource("db/migration/V1__approvals.sql"),
        new ClassPathResource("db/migration/V2__durable_execution.sql")).execute(source);
    var approvals = new JdbcApprovalStore(source, clock);
    approvals.create("approval-1", "run-1", "run-1:restart:1", "orders", LEGACY_FINGERPRINT,
        clock.millis() + 30_000);
    approvals.decide("run-1", "approval-1", ApprovalDecision.APPROVE, "approver");
    ledger = new JdbcRestartLedger(source, clock);
    worker = new DurableGovernance(new PersistentGovernance(approvals, "approver", true),
        new JdbcExecutionStore(source, clock), ledger, DemoFailureMode.NONE);
  }

  @AfterEach
  void close() { source.destroy(); }

  @Test
  void restartFingerprintMatchesThePinned02Release() {
    assertEquals(LEGACY_FINGERPRINT, new InvocationFingerprinter()
        .fingerprint(AgentPermitAdapter.restartInvocation("run-1:restart:1", "orders")).value());
  }

  @Test
  void legacyApprovalExecutesOnceAndRejectsChangedArguments() {
    assertEquals(ActionStatus.DENIED, worker.restart("run-1:restart:1", "payments", "approval-1").status());
    assertEquals(0, ledger.generation("orders"));
    var result = worker.restart("run-1:restart:1", "orders", "approval-1");
    assertEquals(ActionStatus.EXECUTED, result.status());
    assertEquals(result, worker.restart("run-1:restart:1", "orders", "approval-1"));
    assertEquals(1, ledger.generation("orders"));
  }

  @Test
  void receiptRebuiltWithLegacyFingerprintReconcilesWithoutAnotherEffect() {
    var receipt = ledger.restart("run-1:restart:1", LEGACY_FINGERPRINT, "orders");
    var result = worker.reconcile("run-1:restart:1", "orders", "approval-1");
    assertEquals(ActionStatus.EXECUTED, result.status());
    assertEquals(receipt.output(), result.output());
    assertEquals(result, worker.restart("run-1:restart:1", "orders", "approval-1"));
    assertEquals(1, ledger.generation("orders"));
  }
}
