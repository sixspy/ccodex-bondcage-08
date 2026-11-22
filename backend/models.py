"""请求体模型（保证金笼母仓）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BondOpenIn(BaseModel):
    party_key: str = Field(min_length=2, max_length=64)
    party_name: str = Field(min_length=2, max_length=128)
    initial_deposit: float = Field(ge=0)


class DepositIn(BaseModel):
    party_key: str
    amount: float = Field(gt=0)
    memo: str = "手工入金"


class CageFreezeIn(BaseModel):
    party_key: str
    contract_no: str = Field(min_length=2, max_length=64)
    amount: float = Field(gt=0)
    beneficiary: str = Field(min_length=2, max_length=128)
    cage_code: str | None = None


class AttestIn(BaseModel):
    cage_id: int
    outcome: str
    evidence: str = ""


class ReleaseIn(BaseModel):
    cage_id: int
    amount: float | None = None
    payee: str | None = None


class ForfeitIn(BaseModel):
    cage_id: int
    amount: float | None = None
    reason: str = "履约违约"


class RollbackIn(BaseModel):
    forfeit_id: int
    reason: str = "操作员点错合约，罚没回滚"
    # 甲路径为唯一启用口径；显式传乙路径模式按回滚偏航拒写
    mode: str = "整笔提交"


class TopupIn(BaseModel):
    cage_id: int
    amount: float = Field(gt=0)
    mode: str = "同笼扩代"


class SplitIn(BaseModel):
    cage_id: int
    left_amount: float = Field(gt=0)
    right_amount: float = Field(gt=0)
    mode: str = "整笔切分"


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"
