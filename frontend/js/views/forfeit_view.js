export const forfeitViewTemplate = `
<section class="panel">
  <h2>罚没册</h2>
  <p class="hint">
    仅核验违约的笼可罚没。错罚没走「罚没回滚」：甲路径整笔原子提交——罚没作废、笼可用归还、
    账户冻结/余额归还一次落地；乙路径影子单晋升禁用。回滚纪要当日锁死，偏航拒写留痕。
  </p>
  <div class="grid-2">
    <div>
      <div class="form-row">
        <div>
          <label>违约态笼</label>
          <select v-model.number="forfeitForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in failCages" :key="c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }}
            </option>
          </select>
        </div>
        <div>
          <label>金额（空=全额）</label>
          <input type="number" v-model.number="forfeitForm.amount" min="0" step="1000" placeholder="全额"/>
        </div>
        <div style="grid-column:1/-1">
          <label>原因</label>
          <input v-model="forfeitForm.reason"/>
        </div>
      </div>
      <div class="actions">
        <button class="danger" @click="doForfeit">执行罚没</button>
      </div>
      <div v-if="minutes" class="proof-box" style="margin-top:12px">
        回滚纪要 {{ minutes.business_day }} · 口径：{{ minutes.path }}路径（{{ minutes.path_mode }}）
        · 当日封存：<span :class="minutes.sealed ? 'badge pass' : 'badge fail'">{{ minutes.sealed ? '已锁死' : '未封存' }}</span>
        · 影子单晋升：禁用
      </div>
    </div>
    <div>
      <table>
        <thead>
          <tr><th>凭证</th><th>笼</th><th>金额</th><th>原因</th><th>状态</th><th>回滚凭证</th><th>笼代</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="f in forfeits" :key="f.id">
            <td>{{ f.forfeit_ref }}</td>
            <td>{{ f.cage_code }}</td>
            <td>{{ fmt(f.amount) }}</td>
            <td>{{ f.reason }}</td>
            <td>
              <span class="badge" :class="f.status === '已作废' ? 'done' : 'fail'">{{ f.status }}</span>
            </td>
            <td>
              <template v-if="f.status === '已罚没' && role !== 'viewer'">
                <button class="secondary" @click="doRollback(f)">罚没回滚</button>
              </template>
              <template v-else-if="f.status === '已作废'">{{ f.rollback_ref }}</template>
              <span v-else class="hint">—</span>
            </td>
            <td>{{ f.cage_generation }}</td>
            <td>{{ f.created_at }}</td>
          </tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">罚没回滚凭证（审计同源）</h3>
      <table>
        <thead>
          <tr><th>回滚凭证</th><th>原罚没</th><th>归还额</th><th>口径</th><th>操作员</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-if="!rollbacks.length"><td colspan="6" class="hint">暂无回滚</td></tr>
          <tr v-for="r in rollbacks" :key="r.id">
            <td>{{ r.rollback_ref }}</td>
            <td>{{ r.forfeit_ref }}</td>
            <td>{{ fmt(r.amount) }}</td>
            <td>{{ r.path_mode }}</td>
            <td>{{ r.officer }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
