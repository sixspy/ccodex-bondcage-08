"""假绿事故夹具：错罚没已入册、笼可用被抽空、当事人无回滚口。

「假绿」含义：罚没流程表面办结（对账仍平衡、页面显示已罚没），
但笼面可用为 0、账户余额与冻结已扣，且系统不存在任何可审计的回滚路径。
本构建器只通过既有公开接口造数，不直改业务表，供测试与本机文件库脚本共用。
"""
from __future__ import annotations

from typing import Any

from app import app as bondcage_app

DEFAULT_PARTY = "P-FG"
DEFAULT_CAGE = "CG-FG-001"
DEFAULT_CONTRACT = "HT-FG-WRONGFUL-1"
DEPOSIT = 100000.0
FREEZE_AMOUNT = 60000.0


def _auth(client, user: str = "admin") -> dict:
    tok = client.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def build_false_green_accident(
    client,
    party_key: str = DEFAULT_PARTY,
    cage_code: str = DEFAULT_CAGE,
    contract_no: str = DEFAULT_CONTRACT,
) -> dict[str, Any]:
    """造一起完整的错罚没事故，返回事故现场快照。

    步骤全部走真实业务接口：开户入金 → 入笼冻结 → 违约核验 → 全额罚没。
    按笼幂等：同 cage_code 的事故已存在则直接重放现场，不重复造数。
    造数完成后探测回滚口：不存在时 rollback_mouth=False，即假绿坐实。
    """
    h = _auth(client)
    existing = client.get(f"/api/bonds/{party_key}", headers=h)
    if existing.status_code == 200 and any(
        c["cage_code"] == cage_code for c in existing.json()["cages"]
    ):
        bond = existing.json()["account"]
        cage = next(
            c for c in existing.json()["cages"] if c["cage_code"] == cage_code
        )
    else:
        bond = None
        if existing.status_code != 200:
            r = client.post(
                "/api/bonds",
                headers=h,
                json={
                    "party_key": party_key,
                    "party_name": "假绿事故当事人",
                    "initial_deposit": DEPOSIT,
                },
            )
            assert r.status_code == 200, r.text
            bond = r.json()
        r = client.post(
            "/api/cages/freeze",
            headers=h,
            json={
                "party_key": party_key,
                "contract_no": contract_no,
                "amount": FREEZE_AMOUNT,
                "beneficiary": "错点合约相对方",
                "cage_code": cage_code,
            },
        )
        assert r.status_code == 200, r.text
        cage = r.json()
        r = client.post(
            "/api/attestations",
            headers=h,
            json={
                "cage_id": cage["id"],
                "outcome": "违约",
                "evidence": "误操作：错点合约形成的违约核验",
            },
        )
        assert r.status_code == 200, r.text
        r = client.post(
            "/api/forfeits",
            headers=h,
            json={"cage_id": cage["id"], "reason": "操作员点错合约"},
        )
        assert r.status_code == 200, r.text
        cage = client.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
        bond = client.get(f"/api/bonds/{party_key}", headers=h).json()["account"]

    forfeit_rows = client.get("/api/forfeits", headers=h).json()["items"]
    forfeit = next(f for f in forfeit_rows if f["cage_id"] == cage["id"])
    recon = client.get(f"/api/reconcile/{cage_code}", headers=h).json()
    # 回滚口探测：直查 OpenAPI 路由表，不发写请求、不污染现场；实现前路由不存在
    paths = bondcage_app.openapi().get("paths", {})
    rollback_mouth = "/api/forfeits/rollback" in paths
    probe_status = 200 if rollback_mouth else 404

    return {
        "party_key": party_key,
        "cage_id": cage["id"],
        "cage_code": cage_code,
        "forfeit_id": forfeit["id"],
        "forfeit_ref": forfeit["forfeit_ref"],
        "amount": float(forfeit["amount"]),
        "cage_available": float(cage["available"]),
        "cage_status": cage["status"],
        "account_balance": float(bond["balance"]),
        "account_frozen": float(bond["frozen"]),
        "reconcile_balanced": bool(recon["balanced"]),
        "rollback_mouth": rollback_mouth,
        "probe_status": probe_status,
        "false_green": (
            float(cage["available"]) == 0.0
            and forfeit["status"] == "已罚没"
            and not rollback_mouth
        ),
    }
