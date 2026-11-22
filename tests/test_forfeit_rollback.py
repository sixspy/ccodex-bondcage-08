"""罚没回滚测试：先红后绿，禁止跳过失败测。

纪律：
- test_rollback_invalid_failure：罚没回滚无效失败测（含甲路径原子性，失败整笔同滚）。
- test_dual_rollback_contention：双人同回滚争用失败测（抢笼锁，后到只留争用痕）。
两条在回滚能力落地前必须 RED（404），实现后转 GREEN。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from accident_fixture import build_false_green_accident
from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _rollback_count(conn) -> int:
    if not _table_exists(conn, "forfeit_rollbacks"):
        return 0
    return conn.execute("SELECT COUNT(*) c FROM forfeit_rollbacks").fetchone()["c"]


def _scene(c: TestClient):
    return build_false_green_accident(c)


# ---------------------------------------------------------------------------
# 失败测一：罚没回滚无效失败测（红→绿）
# ---------------------------------------------------------------------------
def test_rollback_invalid_failure(monkeypatch):
    """无回滚口时请求必败；实现后必须甲路径一次落地，失败则作废与归还同滚。"""
    c = TestClient(app)
    h = _auth(c)
    scene = _scene(c)
    fid = scene["forfeit_id"]
    # 假绿事故本体坐实：笼面抽空、罚没在册、账户已扣
    assert scene["cage_available"] == 0.0
    assert scene["cage_status"] == "已罚没"
    assert scene["account_frozen"] == 0.0
    assert scene["reconcile_balanced"] is True
    # 实现后回滚口必须存在（实现前这里为 404，测试见红）
    assert scene["rollback_mouth"] is True

    r = c.post("/api/forfeits/rollback", headers=h, json={"forfeit_id": fid})
    # 实现前 404（RED）；实现后必须存在回滚口
    assert r.status_code == 200, f"罚没回滚口缺失或被拒: {r.status_code} {r.text}"
    voucher = r.json()
    assert voucher["forfeit_id"] == fid
    assert voucher["amount"] == scene["amount"]
    assert voucher["rollback_ref"]

    # 罚没册：目标罚没标作废，不是删行、不是改字样
    fr = c.get("/api/forfeits", headers=h).json()["items"]
    target = next(f for f in fr if f["id"] == fid)
    assert target["status"] == "已作废"
    assert target["rollback_ref"] == voucher["rollback_ref"]

    # 入笼页：笼可用同额归还，笼状态回退可再处置
    cage = c.get(f"/api/cages/{scene['cage_id']}", headers=h).json()["cage"]
    assert cage["available"] == pytest.approx(scene["amount"])
    assert cage["status"] == "核验违约"

    # 账户页：冻结与余额同额归还
    bond = c.get(f"/api/bonds/{scene['party_key']}", headers=h).json()["account"]
    assert bond["frozen"] == pytest.approx(scene["amount"])
    assert bond["balance"] == pytest.approx(100000.0)

    # 对账页：作废罚没不计入在册罚没，恒平衡且回滚完整性成立
    recon = c.get(f"/api/reconcile/{scene['cage_code']}", headers=h).json()
    assert recon["balanced"] is True
    assert recon["forfeited"] == pytest.approx(0.0)
    assert recon["rollback_returned"] == pytest.approx(scene["amount"])
    assert recon["rollback_integrity"] is True

    # 审计页：回滚审计已落
    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    assert any(a["action"] == "cage_forfeit_rollback" for a in audits)

    # 原子性：事务中途注入失败，作废与归还必须同滚，五页零改，回滚凭证不留半条
    import rollback_svc  # 延迟导入：实现前模块不存在，保证本测先在上面的 404 见红

    def _boom(*a, **k):
        raise RuntimeError("注入故障：审计写入失败")

    # 再造一起新事故用于故障注入
    scene2 = build_false_green_accident(
        c, party_key="P-FG2", cage_code="CG-FG2-001", contract_no="HT-FG-2"
    )
    monkeypatch.setattr(rollback_svc, "write_audit", _boom)
    with pytest.raises(RuntimeError):
        c.post(
            "/api/forfeits/rollback",
            headers=h,
            json={"forfeit_id": scene2["forfeit_id"]},
        )
    with connect() as conn:
        assert _rollback_count(conn) == 1  # 只有第一笔成功，故障笔零凭证
        f2 = conn.execute(
            "SELECT status FROM forfeits WHERE id=?", (scene2["forfeit_id"],)
        ).fetchone()
        assert f2["status"] == "已罚没"
        cage2 = conn.execute(
            "SELECT available,status FROM cages WHERE id=?", (scene2["cage_id"],)
        ).fetchone()
        assert float(cage2["available"]) == 0.0
        acct2 = conn.execute(
            "SELECT balance,frozen FROM bond_accounts WHERE party_key='P-FG2'"
        ).fetchone()
        assert float(acct2["frozen"]) == 0.0
        assert float(acct2["balance"]) == 40000.0
        # 故障后锁必须释放，不残留
        lock = conn.execute(
            "SELECT 1 FROM cage_locks WHERE lock_key=?",
            (f"cage:{scene2['cage_id']}",),
        ).fetchone()
        assert lock is None


# ---------------------------------------------------------------------------
# 失败测二：双人同回滚争用失败测（红→绿）
# ---------------------------------------------------------------------------
def test_dual_rollback_contention():
    """两人回滚同一罚没须抢笼锁；后到只留争用痕，业务零改。"""
    c = TestClient(app)
    h1 = _auth(c, "admin")
    h2 = _auth(c, "clerk")
    scene = _scene(c)
    fid = scene["forfeit_id"]
    lock_key = f"cage:{scene['cage_id']}"

    # admin 先持笼锁（模拟先到者在途）
    got = c.post("/api/locks/acquire", headers=h1, json={"lock_key": lock_key})
    assert got.status_code == 200

    # clerk 后到回滚：抢锁失败 → 409，只记争用
    r = c.post("/api/forfeits/rollback", headers=h2, json={"forfeit_id": fid})
    assert r.status_code == 409, f"后到者应被笼锁挡住: {r.status_code} {r.text}"

    with connect() as conn:
        # 争用痕
        cont = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=? ORDER BY id DESC",
            (lock_key,),
        ).fetchall()
        assert len(cont) >= 1
        assert cont[0]["actor"] == "clerk"
        # 业务零改：罚没仍在册、笼面仍空、账户未还、无回滚凭证
        f = conn.execute("SELECT status FROM forfeits WHERE id=?", (fid,)).fetchone()
        assert f["status"] == "已罚没"
        cage = conn.execute(
            "SELECT available FROM cages WHERE id=?", (scene["cage_id"],)
        ).fetchone()
        assert float(cage["available"]) == 0.0
        acct = conn.execute(
            "SELECT balance,frozen FROM bond_accounts WHERE party_key=?",
            (scene["party_key"],),
        ).fetchone()
        assert float(acct["frozen"]) == 0.0
        assert float(acct["balance"]) == 40000.0
        assert _rollback_count(conn) == 0

    # 先到者释放锁后完成回滚：一次成功，不允许第二笔
    assert c.post("/api/locks/release", headers=h1, json={"lock_key": lock_key}).status_code == 200
    ok = c.post("/api/forfeits/rollback", headers=h1, json={"forfeit_id": fid})
    assert ok.status_code == 200
    again = c.post("/api/forfeits/rollback", headers=h2, json={"forfeit_id": fid})
    assert again.status_code == 409
    with connect() as conn:
        assert _rollback_count(conn) == 1
        # 锁内偏航（重复回滚）的偏航痕不得被业务回滚吞掉
        yaw = conn.execute(
            "SELECT COUNT(*) c FROM audit_trail WHERE action='rollback_yaw'"
        ).fetchone()["c"]
        assert yaw >= 1
        # 笼锁已释放，无残留
        assert conn.execute(
            "SELECT 1 FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchone() is None


# ---------------------------------------------------------------------------
# 纪要当日锁死与回滚偏航
# ---------------------------------------------------------------------------
def test_minutes_sealed_same_day_and_tamper_is_yaw(monkeypatch):
    c = TestClient(app)
    h = _auth(c)
    scene = _scene(c)
    # 首次回滚触发当日封存
    r1 = c.post(
        "/api/forfeits/rollback", headers=h, json={"forfeit_id": scene["forfeit_id"]}
    )
    assert r1.status_code == 200
    with connect() as conn:
        seals = conn.execute("SELECT * FROM rollback_minutes_seal").fetchall()
    assert len(seals) == 1
    assert seals[0]["business_day"] == "2026-11-22"
    assert len(seals[0]["digest_sha256"]) == 64

    # 再起一起事故，篡改纪要摘要 → 偏航拒写，偏航审计独立留痕
    scene2 = build_false_green_accident(
        c, party_key="P-YAW1", cage_code="CG-YAW1-001", contract_no="HT-YAW-1"
    )
    import rollback_svc

    monkeypatch.setattr(rollback_svc, "minutes_digest", lambda: "0" * 64)
    r2 = c.post(
        "/api/forfeits/rollback",
        headers=h,
        json={"forfeit_id": scene2["forfeit_id"]},
    )
    # 不调用 monkeypatch.undo()：会连带撤销 fixture 对 db.DB_PATH 的重定向；
    # 补丁由 autouse fixture 在用例结束后统一回收
    assert r2.status_code == 409
    assert "偏航" in r2.json()["detail"]
    with connect() as conn:
        f = conn.execute(
            "SELECT status FROM forfeits WHERE id=?", (scene2["forfeit_id"],)
        ).fetchone()
        assert f["status"] == "已罚没"
        assert _rollback_count(conn) == 1  # 仅第一笔
        yaw = conn.execute(
            "SELECT * FROM audit_trail WHERE action='rollback_yaw' ORDER BY id DESC"
        ).fetchall()
        assert len(yaw) >= 1


def test_path_b_shadow_intent_rejected_as_yaw():
    """乙路径影子单晋升禁用：显式请求即偏航，库内不得出现影子单。"""
    c = TestClient(app)
    h = _auth(c)
    scene = _scene(c)
    r = c.post(
        "/api/forfeits/rollback",
        headers=h,
        json={"forfeit_id": scene["forfeit_id"], "mode": "影子单晋升"},
    )
    assert r.status_code == 409
    assert "偏航" in r.json()["detail"]
    with connect() as conn:
        # 影子单表不得存在；罚没与笼面零改
        assert _table_exists(conn, "rollback_shadow_orders") is False
        f = conn.execute(
            "SELECT status FROM forfeits WHERE id=?", (scene["forfeit_id"],)
        ).fetchone()
        assert f["status"] == "已罚没"
        assert _rollback_count(conn) == 0


# ---------------------------------------------------------------------------
# 五页账实同源
# ---------------------------------------------------------------------------
def test_five_views_same_source_after_rollback():
    c = TestClient(app)
    h = _auth(c)
    scene = _scene(c)
    rb = c.post(
        "/api/forfeits/rollback", headers=h, json={"forfeit_id": scene["forfeit_id"]}
    ).json()

    # 罚没页
    forfeits = c.get("/api/forfeits", headers=h).json()["items"]
    f = next(x for x in forfeits if x["id"] == scene["forfeit_id"])
    assert f["status"] == "已作废"
    # 入笼页
    cage = c.get(f"/api/cages/{scene['cage_id']}", headers=h).json()
    assert cage["cage"]["available"] == pytest.approx(scene["amount"])
    assert any(x["id"] == scene["forfeit_id"] and x["status"] == "已作废" for x in cage["forfeits"])
    # 账户页
    bond = c.get(f"/api/bonds/{scene['party_key']}", headers=h).json()["account"]
    assert bond["balance"] - bond["frozen"] == pytest.approx(100000.0 - scene["amount"])
    # 对账页
    recon = c.get(f"/api/reconcile/{scene['cage_code']}", headers=h).json()
    assert recon["balanced"] is True
    assert recon["rollback_integrity"] is True
    proof = c.get(f"/api/proof/{scene['party_key']}", headers=h).json()
    assert proof["frozen_matches_live"] is True
    # 审计页
    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    assert any(a["action"] == "cage_forfeit_rollback" and rb["rollback_ref"] in a["payload"] for a in audits)
    # 回滚凭证只读面
    rbs = c.get("/api/forfeits/rollbacks", headers=h).json()["items"]
    assert any(x["rollback_ref"] == rb["rollback_ref"] for x in rbs)
    minutes = c.get("/api/forfeits/rollback-minutes", headers=h).json()
    assert minutes["business_day"] == "2026-11-22"
    assert minutes["path"] == "甲"
    assert minutes["sealed"] is True


def test_viewer_cannot_rollback():
    c = TestClient(app)
    scene = build_false_green_accident(c)
    hv = _auth(c, "viewer")
    r = c.post(
        "/api/forfeits/rollback", headers=hv, json={"forfeit_id": scene["forfeit_id"]}
    )
    assert r.status_code == 403


def test_phony_void_without_voucher_is_false_green():
    """只改罚没页撤销字样、不还资金不写凭证：对账必须判假绿失败。"""
    c = TestClient(app)
    h = _auth(c)
    scene = build_false_green_accident(c)
    with connect() as conn:
        # 模拟违规伪回滚：仅把状态改成已作废，笼面与账户分文未动、无回滚凭证
        conn.execute(
            "UPDATE forfeits SET status='已作废' WHERE id=?",
            (scene["forfeit_id"],),
        )
        conn.commit()
    recon = c.get(f"/api/reconcile/{scene['cage_code']}", headers=h).json()
    assert recon["rollback_integrity"] is False
    assert recon["balanced"] is False
    assert scene["forfeit_id"] in recon["missing_voucher_forfeit_ids"]
    # 该伪作废无法通过正规回滚救济（正规口只认在册罚没）
    r = c.post(
        "/api/forfeits/rollback", headers=h, json={"forfeit_id": scene["forfeit_id"]}
    )
    assert r.status_code == 409
    # 审计可见偏航
    audits = c.get("/api/audit?limit=50", headers=h).json()["items"]
    assert any(a["action"] == "rollback_yaw" for a in audits)
