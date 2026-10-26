"""Credits：退回已按旧规则扣掉的系统原因结束的运行。

原因不明的结束（incomplete）、进程退出后由后台对账改成中断的运行，改为不扣。

Revision ID: b3e7d1f4a982
Revises: a9c4e7b2d150
Create Date: 2026-10-26 Asia/Shanghai
"""

from alembic import op

revision = "b3e7d1f4a982"
down_revision = "a9c4e7b2d150"
branch_labels = None
depends_on = None

# 与 app.services.agent.trajectory_reconciliation.ORPHANED_RUN_ERROR_MESSAGE 一致；迁移不引用应用代码
_ORPHANED_RUN_ERROR_MESSAGE = "运行进程已退出，未写入终态"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE credit_ledger SET charged = false, credits = 0
        WHERE kind = 'run' AND charged
          AND (
            run_status = 'incomplete'
            OR run_id IN (
              SELECT id FROM agent_sessions
              WHERE status = 'interrupted' AND error_message = '{_ORPHANED_RUN_ERROR_MESSAGE}'
            )
          )
        """
    )


def downgrade() -> None:
    # 退回的金额不再恢复
    pass
