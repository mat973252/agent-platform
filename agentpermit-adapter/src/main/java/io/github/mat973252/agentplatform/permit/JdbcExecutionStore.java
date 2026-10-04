package io.github.mat973252.agentplatform.permit;

import io.github.agentpermit4j.execution.ToolExecutionResult;
import io.github.agentpermit4j.core.DecisionOutcome;
import java.time.Clock;
import java.util.List;
import javax.sql.DataSource;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

public final class JdbcExecutionStore {
  private final JdbcTemplate jdbc;
  private final TransactionTemplate transaction;
  private final Clock clock;
  private static final RowMapper<OperationRecord> ROW = (rs, row) -> new OperationRecord(
      rs.getString("operation_id"), rs.getString("approval_id"), rs.getString("fingerprint"), rs.getString("status"),
      rs.getString("reason_code"), rs.getString("output"), rs.getLong("started_at"), rs.getObject("finished_at", Long.class),
      rs.getString("closed_by"), rs.getString("closed_reason"), rs.getLong("reconciliation_version"), rs.getLong("delivered_version"));

  public JdbcExecutionStore(DataSource source, Clock clock) {
    jdbc = new JdbcTemplate(source);
    transaction = new TransactionTemplate(new DataSourceTransactionManager(source));
    this.clock = clock;
  }

  boolean acquire(String operationId, String approvalId, String fingerprint, Runnable consumeApproval) {
    try {
      transaction.executeWithoutResult(status -> {
        insert(operationId, approvalId, fingerprint, "IN_PROGRESS", "EXECUTION_IN_PROGRESS");
        consumeApproval.run();
      });
      return true;
    } catch (DuplicateKeyException existing) {
      return false;
    }
  }

  private void insert(String operationId, String approvalId, String fingerprint, String state, String reason) {
    jdbc.update("""
        INSERT INTO platform_executions(operation_id,approval_id,fingerprint,status,reason_code,started_at)
        VALUES (?,?,?,?,?,?)
        """, operationId, approvalId, fingerprint, state, reason, clock.millis());
  }

  public OperationRecord find(String operationId) {
    var rows = jdbc.query("SELECT * FROM platform_executions WHERE operation_id=?", ROW, operationId);
    return rows.isEmpty() ? null : rows.getFirst();
  }

  public OperationRecord get(String operationId) {
    var record = find(operationId);
    if (record == null) throw new IllegalStateException("Execution record was not found");
    return record;
  }

  OperationRecord freezeUnknown(String operationId, String approvalId, String fingerprint) {
    try {
      insert(operationId, approvalId, fingerprint, "UNKNOWN", "EXECUTION_OUTCOME_UNKNOWN");
    } catch (DuplicateKeyException existing) {
      // A reconciliation fence never grants another worker ownership.
    }
    return get(operationId);
  }

  OperationRecord complete(String operationId, ToolExecutionResult result) {
    if (result.decision().outcome() == DecisionOutcome.EXECUTED) {
      confirm(operationId, result.output(), result.decision().reasonCode());
    } else {
      jdbc.update("UPDATE platform_executions SET status='UNKNOWN',reason_code='EXECUTION_OUTCOME_UNKNOWN' WHERE operation_id=? AND status='IN_PROGRESS'", operationId);
    }
    return get(operationId);
  }

  OperationRecord confirm(String operationId, String output, String reason) {
    jdbc.update("""
        UPDATE platform_executions SET status='SUCCEEDED',reason_code=?,output=?,finished_at=?
        WHERE operation_id=? AND status IN ('IN_PROGRESS','UNKNOWN')
        """, reason, output, clock.millis(), operationId);
    return get(operationId);
  }

  public void requestCheck(String operationId) {
    int changed = jdbc.update("UPDATE platform_executions SET reconciliation_version=reconciliation_version+1 WHERE operation_id=? AND status IN ('IN_PROGRESS','UNKNOWN','SUCCEEDED')", operationId);
    if (changed == 0) throw new IllegalStateException("Execution is not available for reconciliation");
  }

  public void closeUnknown(String operationId, String actor, String reason) {
    if (reason == null || reason.isBlank() || reason.length() > 512) throw new IllegalArgumentException("A closure reason of 1 to 512 characters is required");
    int changed = jdbc.update("""
        UPDATE platform_executions SET status='CLOSED_UNKNOWN',reason_code='OPERATOR_CLOSED_UNKNOWN',
          closed_by=?,closed_reason=?,finished_at=?,reconciliation_version=reconciliation_version+1
        WHERE operation_id=? AND status IN ('IN_PROGRESS','UNKNOWN')
        """, actor, reason, clock.millis(), operationId);
    if (changed == 0) {
      var record = get(operationId);
      if (!record.status().equals("CLOSED_UNKNOWN") || !actor.equals(record.closedBy()) || !reason.equals(record.closedReason())) {
        throw new IllegalStateException("Execution is already confirmed or closed differently");
      }
    }
  }

  public List<OperationRecord> undelivered() {
    return jdbc.query("SELECT * FROM platform_executions WHERE reconciliation_version>delivered_version ORDER BY started_at LIMIT 100", ROW);
  }

  public void delivered(String operationId, long version) {
    jdbc.update("UPDATE platform_executions SET delivered_version=? WHERE operation_id=? AND delivered_version<?", version, operationId, version);
  }
}
