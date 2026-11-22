"""罚没回滚（甲路径：整笔原子提交）。

口径锁死见 docs/罚没回滚纪要_2026-11-22.md：
同一事务内完成「罚没作废 + 笼可用归还 + 账户冻结/余额归还 + 回滚凭证 + 审计」，
任一步失败整笔回滚。乙路径（影子单晋升）禁用，请求即回滚偏航。
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import ROOT, connect, now_iso, write_audit
from locks import release, try_acquire
from models import RollbackIn

router = APIRouter(tags=["forfeit-rollback"])

BUSINESS_DAY = "2026-11-22"
MINUTES_PATH = ROOT / "docs" / f"罚没回滚纪要_{BUSINESS_DAY}.md"
# 甲路径唯一启用；乙路径字样出现即偏航
PATH_A = "整笔提交"
ALLOWED_MODES = {PATH_A, "甲", "甲路径", "整笔原子提交"}
PATH_B_TOKENS = ("影子", "晋升", "乙")
PATH_MODE_STORED = "甲-整笔原子提交"


def minutes_digest() -> str:
    """对当日纪要原文做 SHA-256；文件缺失返回空串，由偏航守卫拒写。"""
    if not MINUTES_PATH.exists():
        return ""
    return hashlib.sha256(MINUTES_PATH.read_bytes()).hexdigest()


def _record_yaw(conn, user: str, detail: str, target: str = "罚没回滚") -> None:
    """回滚偏航：独立审计提交，业务写入一律拒绝。

    偏航审计在独立事务提交，随后开启新事务供外层 finally 释放锁，
    保证偏航痕不被业务异常的整笔回滚波及。
    """
    write_audit(
        conn,
        user,
        "rollback_yaw",
        target,
        {"detail": detail, "business_day": BUSINESS_DAY},
    )
    conn.commit()
    # 提交后连接自动进入新事务；开启它，使 finally 的 release/commit 有事务可提交
    conn.execute("SELECT 1")


def _reject_yaw(user: str, detail: str, target: str = "罚没回滚") -> None:
    with connect() as conn:
        _record_yaw(conn, user, detail, target)
    raise HTTPException(409, f"回滚偏航，拒写：{detail}")


def _ensure_seal(conn, user: str, digest: str) -> None:
    """当日首次回滚前封存纪要摘要；封存后摘要不符即偏航。"""
    if not digest:
        _record_yaw(conn, user, "罚没回滚纪要缺失或不可读")
        raise HTTPException(409, "回滚偏航，拒写：罚没回滚纪要缺失")
    row = conn.execute(
        "SELECT digest_sha256 FROM rollback_minutes_seal WHERE business_day=?",
        (BUSINESS_DAY,),
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO rollback_minutes_seal(
                   business_day,minutes_path,digest_sha256,sealed_at,sealed_by)
               VALUES (?,?,?,?,?)""",
            (BUSINESS_DAY, str(MINUTES_PATH), digest, now_iso(), user),
        )
    elif row["digest_sha256"] != digest:
        _record_yaw(
            conn,
            user,
            "纪要摘要与当日封存不一致，疑似纪要被篡改",
        )
        raise HTTPException(409, "回滚偏航，拒写：纪要当日锁死后被改动")


@router.post("/api/forfeits/rollback")
def rollback_forfeit(body: RollbackIn, authorization: AuthHeader = None):
    user = require_writer(authorization)

    # --- 锁前偏航守卫：模式与纪要，独立留痕后拒写 ---
    mode = (body.mode or PATH_A).strip()
    if any(tok in mode for tok in PATH_B_TOKENS) or mode not in ALLOWED_MODES:
        _reject_yaw(user, f"乙路径/影子单晋升禁用，收到 mode={mode}")
    digest = minutes_digest()

    with connect() as conn:
        _ensure_seal(conn, user, digest)

        forfeit = conn.execute(
            "SELECT * FROM forfeits WHERE id=?", (body.forfeit_id,)
        ).fetchone()
        if not forfeit:
            raise HTTPException(404, "罚没凭证不存在")
        lock_key = f"cage:{forfeit['cage_id']}"
        holder = user
        if not try_acquire(conn, lock_key, holder):
            # 后到者：争用痕已由 try_acquire 落 contention_log，业务零改
            conn.commit()
            raise HTTPException(409, "争用：后到只记争用，不写业务")
        try:
            return _do_rollback(conn, user, body, forfeit, lock_key, digest)
        except HTTPException:
            # 业务规则拒绝（含偏航）：偏航痕已独立提交；未提交的业务改动（若有）回滚
            conn.rollback()
            raise
        except Exception:
            # 甲路径纪律：事务故障则作废与归还同滚，业务表零改
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, holder)
            conn.commit()


def _do_rollback(conn, user: str, body: RollbackIn, forfeit, lock_key: str, digest: str) -> dict:
    cage = conn.execute("SELECT * FROM cages WHERE id=?", (forfeit["cage_id"],)).fetchone()
    if not cage:
        raise HTTPException(404, "笼不存在")
    if forfeit["status"] != "已罚没":
        _record_yaw(conn, user, f"罚没 {forfeit['id']} 状态={forfeit['status']}，不可重复回滚")
        raise HTTPException(409, "回滚偏航，拒写：罚没已作废，禁止重复回滚")
    if int(forfeit["cage_generation"]) != int(cage["generation"]):
        _record_yaw(
            conn,
            user,
            f"笼代不一致：罚末代 {forfeit['cage_generation']} vs 当前代 {cage['generation']}",
            target=cage["cage_code"],
        )
        raise HTTPException(409, "回滚偏航，拒写：笼代不一致")

    amount = float(forfeit["amount"])
    now = now_iso()
    rollback_ref = f"RB-{forfeit['forfeit_ref']}-{BUSINESS_DAY.replace('-', '')}"
    cage_id = forfeit["cage_id"]

    # 1) 罚没作废（不删行、不只改字样）
    conn.execute(
        """UPDATE forfeits
           SET status='已作废', voided_at=?, voided_by=?, rollback_ref=?
           WHERE id=?""",
        (now, user, rollback_ref, forfeit["id"]),
    )

    # 2) 笼面归还：可用额加回；同代无在册罚没则回退核验违约，否则保持部分罚没
    active_forfeits = conn.execute(
        """SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM forfeits
           WHERE cage_id=? AND cage_generation=? AND status='已罚没'""",
        (cage_id, int(cage["generation"])),
    ).fetchone()
    new_available = round(float(cage["available"]) + amount, 6)
    new_status = "核验违约" if active_forfeits["c"] == 0 else "部分罚没"
    conn.execute(
        "UPDATE cages SET available=?, status=? WHERE id=?",
        (new_available, new_status, cage_id),
    )

    # 3) 账户归还：冻结与余额同额加回
    conn.execute(
        """UPDATE bond_accounts
           SET frozen = frozen + ?, balance = balance + ?
           WHERE party_key=?""",
        (amount, amount, cage["party_key"]),
    )

    # 4) 回滚凭证（含纪要摘要，便于审计同源核验）
    cur = conn.execute(
        """INSERT INTO forfeit_rollbacks(
               forfeit_id,cage_id,amount,rollback_ref,reason,business_day,
               path_mode,minutes_digest,officer,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            forfeit["id"],
            cage_id,
            amount,
            rollback_ref,
            body.reason or "操作员点错合约，罚没回滚",
            BUSINESS_DAY,
            PATH_MODE_STORED,
            digest,
            user,
            now,
        ),
    )

    # 5) 审计（write_audit 被测试注入故障时抛错，触发整笔回滚）
    write_audit(
        conn,
        user,
        "cage_forfeit_rollback",
        cage["cage_code"],
        {
            "rollback_id": cur.lastrowid,
            "rollback_ref": rollback_ref,
            "forfeit_id": forfeit["id"],
            "forfeit_ref": forfeit["forfeit_ref"],
            "amount": amount,
            "cage_generation": int(cage["generation"]),
            "path_mode": PATH_MODE_STORED,
            "minutes_digest": digest,
            "lock_key": lock_key,
        },
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM forfeit_rollbacks WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return dict(row)


@router.get("/api/forfeits/rollbacks")
def list_rollbacks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.*, c.cage_code, c.contract_no, f.forfeit_ref
               FROM forfeit_rollbacks r
               JOIN cages c ON c.id=r.cage_id
               JOIN forfeits f ON f.id=r.forfeit_id
               ORDER BY r.id DESC LIMIT 200"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/forfeits/rollback-minutes")
def rollback_minutes(authorization: AuthHeader = None):
    require_user(authorization)
    digest = minutes_digest()
    with connect() as conn:
        seal = conn.execute(
            "SELECT * FROM rollback_minutes_seal WHERE business_day=?",
            (BUSINESS_DAY,),
        ).fetchone()
    return {
        "business_day": BUSINESS_DAY,
        "path": "甲",
        "path_mode": PATH_MODE_STORED,
        "minutes_path": str(MINUTES_PATH),
        "digest_sha256": digest,
        "sealed": seal is not None and seal["digest_sha256"] == digest,
        "seal": dict(seal) if seal else None,
        "shadow_orders_enabled": False,
    }
