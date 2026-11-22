# 保证金笼

> 遵守 `ops/底座生成前必读_效率与技术栈.md`（≥2000 行像产品）。  
> FastAPI + Vue 3 + SQLite；`docker compose up -d --build` 一键起。  
> **禁止同构换皮**：主链路不是「占位→确认入册」也不是「入轨→裁定赔付」也不是「业务窗裁切物化」。

## 骨架一句话

保证金先入笼冻结，履约核验通过才解笼拨付，违约进罚没册，笼代与拨付罚没对账。

## 基本信息

| 项 | 值 |
|----|-----|
| 产品名 | 保证金笼 |
| 目录 | `11-bondcage` |
| 接口 | http://127.0.0.1:11318 |
| 库 | `data/app.db` |
| 账号 | admin / clerk / viewer ，密码均为 123456（viewer 只读） |

## 厚度

| 项 | 内容 |
|----|------|
| 业务 LOC | **≥2900**（py+前端，不含 vue.global / Docker / README；自检约 2984） |
| 后端模块 | db / auth / locks / bonds_svc / cages_svc / attest_svc / release_svc / forfeit_svc / reconcile_svc / audit_svc / topup_svc / split_svc |
| 前端视图 | 保证金户 / 入笼冻结 / 履约核验 / 解笼拨付 / 罚没册 / 对账互证 / 审计争用 / 增补拆笼空桩（8 页） |
| 空桩 | topup（增补入笼）、split（拆笼分拨）——前后端均有 |

## 主链路

开户入金 → 按合约入笼冻结（笼代）→ 履约核验通过 → 解笼拨付到收款方 / 违约罚没 → 笼代余额与拨付+罚没对账

## 互斥切口候选

- 解笼失败整笔回滚 vs 影子拨付后晋升
- 罚没立即入册 vs 影子罚没待复核晋升
- 增补同笼扩代 vs 新笼桥接双写
- 拆笼整笔事务切分 vs 影子子笼后晋升

## 罚没回滚（甲路径，当日锁死）

口径见 `docs/罚没回滚纪要_2026-11-22.md`，**只走甲路径整笔原子提交，乙路径影子单晋升禁用**：

- `POST /api/forfeits/rollback` 同一事务内完成罚没作废、笼可用归还、账户冻结/余额归还、回滚凭证、审计，失败整笔同滚。
- 纪要 SHA-256 摘要在业务日首次回滚时写入 `rollback_minutes_seal`，封存后改动即回滚偏航（`rollback_yaw` 审计独立留痕，业务拒写）。
- 两人同回滚一罚没抢 `cage:{id}` 笼锁，后到者仅留 contention 争用痕、409 零改。
- 五页同源：罚没页/入笼页/账户页/对账页/审计页共用同一库；对账页 `rollback_integrity` 专查「无凭证假作废」假绿。
- 假绿事故夹具：`python3 -I scripts/seed_false_green.py`（本机文件库造数，库路径默认 `data/app.db`，只读时回退同目录 `data/app-demo.db`）。
- 测试：`tests/test_forfeit_rollback.py`（罚没回滚无效失败测、双人争用失败测先红后绿，共 7 条）。

## 启动

```powershell
cd projects/11-bondcage
docker compose up -d --build
```

浏览器：http://127.0.0.1:11318/

本机开发：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 10608
python -m pytest -q
```
