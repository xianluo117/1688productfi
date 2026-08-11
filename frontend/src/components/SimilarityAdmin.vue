<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { api } from "../api";

const emit = defineEmits(["back"]);
const props = defineProps({ users: { type: Array, default: () => [] } });
const configs = ref([]);
const candidates = ref([]);
const stats = reactive({
  pending: 0,
  processing: 0,
  completed: 0,
  failed: 0,
  review_pending: 0,
  cancelled: 0,
  completed_last_minute: 0,
  queue_state: "running",
  concurrency: 4,
  auto_vectorization_enabled: true,
});
const scopes = ref([]);
const selectedOwnerId = ref(null);
const selectedGroupIds = ref([]);
const selectedTemuGroupIds = ref([]);
let restoringScope = true;
const failedTasks = ref([]);
const selectedCandidateIds = ref([]);
const batchDeciding = ref(false);
const testing = ref(false);
const testResult = ref(null);
const saving = ref(false);
let refreshTimer;
const form = reactive({
  id: null,
  name: "Gemini 图片向量模型",
  base_url: "https://generativelanguage.googleapis.com/v1beta",
  api_key: "",
  model_name: "gemini-embedding-2",
  output_dimension: 1536,
  response_path: "embedding.values",
  timeout_seconds: 60,
  top_k: 100,
  phash_auto_distance: 4,
  review_threshold: 0.86,
  auto_merge_threshold: 0.94,
  auto_merge_enabled: false,
  activate: true,
});

const productScopes = computed(() =>
  scopes.value.filter((group) => group.platform === "1688"),
);
const temuScopes = computed(() =>
  scopes.value.filter((group) => group.platform === "temu"),
);
const selectedScopeGroups = computed(() =>
  scopes.value.filter((group) =>
    group.platform === "1688"
      ? selectedGroupIds.value.includes(group.id)
      : selectedTemuGroupIds.value.includes(group.id),
  ),
);
const allCandidatesSelected = computed(
  () =>
    candidates.value.length > 0 &&
    candidates.value.every((candidate) =>
      selectedCandidateIds.value.includes(
        `${candidate.candidate_type}:${candidate.id}`,
      ),
    ),
);
const selectedReadyCount = computed(() =>
  selectedScopeGroups.value.reduce(
    (total, group) => total + group.ready_product_count,
    0,
  ),
);
const scopeDescription = computed(() => {
  const count =
    selectedGroupIds.value.length + selectedTemuGroupIds.value.length;
  return count
    ? `所选用户的 ${count} 个跨平台分组`
    : "全部用户的全部 1688 与 Temu 商品";
});

async function loadScopes() {
  if (!selectedOwnerId.value) {
    scopes.value = [];
    return;
  }
  const configParam = form.id ? `&config_id=${form.id}` : "";
  scopes.value = await api(
    `/api/similarity/admin/scopes?owner_id=${selectedOwnerId.value}${configParam}`,
  );
  selectedGroupIds.value = selectedGroupIds.value.filter((id) =>
    productScopes.value.some((group) => group.id === id),
  );
  selectedTemuGroupIds.value = selectedTemuGroupIds.value.filter((id) =>
    temuScopes.value.some((group) => group.id === id),
  );
}

async function saveScopePreference() {
  if (restoringScope || !selectedOwnerId.value) return;
  await api("/api/similarity/admin/scope-preference", {
    method: "PUT",
    body: JSON.stringify(scopePayload()),
  });
}

async function load() {
  const preferenceRequest = restoringScope
    ? api("/api/similarity/admin/scope-preference")
    : Promise.resolve(null);
  const [loadedConfigs, loadedCandidates, loadedTasks, preference] =
    await Promise.all([
      api("/api/similarity/admin/configs"),
      api("/api/similarity/admin/candidates?status=pending&limit=100"),
      api("/api/similarity/admin/tasks?status=failed&limit=100"),
      preferenceRequest,
    ]);
  configs.value = loadedConfigs;
  candidates.value = loadedCandidates;
  failedTasks.value = loadedTasks;
  if (preference) {
    selectedOwnerId.value = preference.owner_id || props.users[0]?.id || null;
    selectedGroupIds.value = preference.group_ids;
    selectedTemuGroupIds.value = preference.temu_group_ids;
  }
  Object.assign(stats, await api("/api/similarity/admin/stats"));
  selectedCandidateIds.value = selectedCandidateIds.value.filter((id) =>
    candidates.value.some(
      (candidate) => `${candidate.candidate_type}:${candidate.id}` === id,
    ),
  );
  if (!form.id && configs.value.length) selectConfig(configs.value[0]);
  if (!selectedOwnerId.value && props.users.length) {
    selectedOwnerId.value = props.users[0].id;
  }
  if (restoringScope && selectedOwnerId.value) {
    await loadScopes();
    restoringScope = false;
  }
}

function selectConfig(config) {
  Object.assign(form, {
    ...config,
    api_key: "",
    activate: config.is_active,
  });
}

function createNew() {
  Object.assign(form, {
    id: null,
    name: "Gemini 图片向量模型",
    base_url: "https://generativelanguage.googleapis.com/v1beta",
    api_key: "",
    model_name: "gemini-embedding-2",
    output_dimension: 1536,
    response_path: "embedding.values",
    timeout_seconds: 60,
    top_k: 100,
    phash_auto_distance: 4,
    review_threshold: 0.86,
    auto_merge_threshold: 0.94,
    auto_merge_enabled: false,
    activate: true,
  });
}

async function saveConfig() {
  saving.value = true;
  try {
    const path = form.id
      ? `/api/similarity/admin/configs/${form.id}`
      : "/api/similarity/admin/configs";
    const method = form.id ? "PUT" : "POST";
    await api(path, {
      method,
      body: JSON.stringify({
        ...form,
        api_key: form.api_key || null,
        owner_id: selectedOwnerId.value,
        group_ids: selectedGroupIds.value,
      }),
    });
    createNew();
    await load();
  } finally {
    saving.value = false;
  }
}

async function saveQueueSettings() {
  Object.assign(
    stats,
    await api("/api/similarity/admin/queue", {
      method: "PUT",
      body: JSON.stringify({
        concurrency: stats.concurrency,
        auto_vectorization_enabled: stats.auto_vectorization_enabled,
      }),
    }),
  );
}

async function controlQueue(action) {
  const labels = {
    pause: "暂停领取新任务",
    resume: "继续处理",
    stop: "停止并清空等待任务",
  };
  if (
    action === "stop" &&
    !window.confirm("确认停止并取消全部等待任务？正在处理的任务会自然完成。")
  )
    return;
  Object.assign(
    stats,
    await api(`/api/similarity/admin/queue/${action}`, { method: "POST" }),
  );
  await load();
  if (action === "stop") window.alert(labels[action]);
}

async function retryTask(taskId) {
  await api(`/api/similarity/admin/tasks/id/${taskId}/retry`, {
    method: "POST",
  });
  await load();
}

async function retryAllFailed() {
  if (
    !window.confirm(`确认重新处理全部 ${failedTasks.value.length} 个失败任务？`)
  )
    return;
  await api("/api/similarity/admin/tasks/retry-failed", { method: "POST" });
  await load();
}

async function clearFailedTasks() {
  if (
    !window.confirm(
      `确认清空全部 ${failedTasks.value.length} 条失败记录？此操作不会删除商品。`,
    )
  )
    return;
  await api("/api/similarity/admin/tasks/failed", { method: "DELETE" });
  await load();
}

async function testConfig(config) {
  testing.value = true;
  testResult.value = null;
  try {
    testResult.value = await api(
      `/api/similarity/admin/configs/${config.id}/test`,
      {
        method: "POST",
        body: JSON.stringify({
          base_url: form.base_url,
          api_key: form.api_key.trim() || null,
          model_name: form.model_name,
          output_dimension: form.output_dimension,
          response_path: form.response_path,
          timeout_seconds: form.timeout_seconds,
        }),
      },
    );
  } catch (error) {
    testResult.value = { success: false, message: error.message };
  } finally {
    testing.value = false;
  }
}

function scopePayload() {
  const hasSelection =
    selectedGroupIds.value.length || selectedTemuGroupIds.value.length;
  return {
    owner_id: hasSelection ? selectedOwnerId.value : null,
    group_ids: selectedGroupIds.value,
    temu_group_ids: selectedTemuGroupIds.value,
  };
}

async function enqueueSelectedScope(config) {
  if (
    !window.confirm(
      `确认手动启动 ${scopeDescription.value} 的向量化？已有有效任务和向量不会重复创建。`,
    )
  )
    return;
  await api(`/api/similarity/admin/configs/${config.id}/enqueue`, {
    method: "POST",
    body: JSON.stringify(scopePayload()),
  });
  await load();
}

async function rebuild(config) {
  if (
    !window.confirm(
      `确认重新处理 ${scopeDescription.value}？会重建当前模型的任务、向量和未确认候选，但保留人工确认关系与审计记录。`,
    )
  )
    return;
  await api(`/api/similarity/admin/configs/${config.id}/rebuild`, {
    method: "POST",
    body: JSON.stringify(scopePayload()),
  });
  await load();
}

async function decide(candidate, decision) {
  await api(
    `/api/similarity/admin/candidates/${candidate.candidate_type}/${candidate.id}/decision`,
    {
      method: "POST",
      body: JSON.stringify({ decision }),
    },
  );
  const typedId = `${candidate.candidate_type}:${candidate.id}`;
  selectedCandidateIds.value = selectedCandidateIds.value.filter(
    (id) => id !== typedId,
  );
  await load();
}

function toggleSelectAll() {
  selectedCandidateIds.value = allCandidatesSelected.value
    ? []
    : candidates.value.map(
        (candidate) => `${candidate.candidate_type}:${candidate.id}`,
      );
}

async function batchDecide(decision) {
  if (!selectedCandidateIds.value.length) return;
  const label = decision === "approve" ? "确认同款并合并" : "拒绝";
  if (
    !window.confirm(
      `确认批量${label}已选择的 ${selectedCandidateIds.value.length} 组候选？`,
    )
  )
    return;
  batchDeciding.value = true;
  try {
    await api("/api/similarity/admin/candidates/batch-decision", {
      method: "POST",
      body: JSON.stringify({
        candidate_ids: selectedCandidateIds.value,
        decision,
      }),
    });
    selectedCandidateIds.value = [];
    await load();
  } finally {
    batchDeciding.value = false;
  }
}

function formatScore(value) {
  return value === null || value === undefined ? "-" : Number(value).toFixed(4);
}

watch(selectedOwnerId, async () => {
  if (!restoringScope) {
    selectedGroupIds.value = [];
    selectedTemuGroupIds.value = [];
    await loadScopes();
    await saveScopePreference();
  }
});
watch([selectedGroupIds, selectedTemuGroupIds], saveScopePreference, {
  deep: true,
});

onMounted(async () => {
  await load();
  refreshTimer = window.setInterval(load, 5000);
});

onUnmounted(() => window.clearInterval(refreshTimer));
</script>

<template>
  <section class="content similarity-admin">
    <header>
      <div>
        <h2>相似度检索</h2>
        <span>配置 Gemini 图片向量、运行范围、任务队列与候选审核</span>
      </div>
      <button class="secondary" @click="emit('back')">返回商品</button>
    </header>

    <section class="queue-panel">
      <div>
        <strong>队列状态：{{ stats.queue_state }}</strong>
        <span
          >安全暂停不会领取新任务；停止只取消等待任务，在途任务自然完成。</span
        >
      </div>
      <label class="switch-field"
        ><input
          v-model="stats.auto_vectorization_enabled"
          type="checkbox"
        />新采集图片自动向量化</label
      >
      <label
        >并发数<input
          v-model.number="stats.concurrency"
          type="number"
          min="1"
          max="32"
      /></label>
      <button @click="saveQueueSettings">保存处理设置</button>
      <button class="secondary" @click="controlQueue('pause')">暂停</button>
      <button @click="controlQueue('resume')">继续</button>
      <button class="danger" @click="controlQueue('stop')">
        停止并清空等待
      </button>
    </section>

    <div class="similarity-stats">
      <div>
        <strong>{{ stats.pending }}</strong
        ><span>等待处理</span>
      </div>
      <div>
        <strong>{{ stats.processing }}</strong
        ><span>处理中</span>
      </div>
      <div>
        <strong>{{ stats.completed }}</strong
        ><span>已完成</span>
      </div>
      <div>
        <strong>{{ stats.failed }}</strong
        ><span>失败</span>
      </div>
      <div>
        <strong>{{ stats.cancelled }}</strong
        ><span>已取消</span>
      </div>
      <div>
        <strong>{{ stats.completed_last_minute }}</strong
        ><span>近一分钟完成</span>
      </div>
      <div>
        <strong>{{ stats.review_pending }}</strong
        ><span>待审核</span>
      </div>
    </div>

    <section class="review-panel scope-panel">
      <div class="section-heading">
        <div>
          <h3>向量化运行范围</h3>
          <span
            >分组选择按管理员账号保存；不选择分组时处理两个平台的全部商品</span
          >
        </div>
      </div>
      <label class="owner-select"
        >用户
        <select v-model="selectedOwnerId">
          <option v-for="user in users" :key="user.id" :value="user.id">
            {{ user.username }}
          </option>
        </select>
      </label>
      <h4>1688 分组</h4>
      <div class="scope-group-list">
        <label
          v-for="group in productScopes"
          :key="`1688-${group.id}`"
          class="scope-group-item"
        >
          <input v-model="selectedGroupIds" type="checkbox" :value="group.id" />
          <span
            ><strong>{{ group.name }}</strong
            ><small>
              共 {{ group.product_count }}，图片就绪
              {{ group.ready_product_count }}，已处理
              {{ group.processed_product_count }}，待处理
              {{ group.pending_product_count }}
            </small></span
          >
        </label>
        <p v-if="!productScopes.length" class="empty">该用户暂无 1688 分组</p>
      </div>
      <h4>Temu 分组</h4>
      <div class="scope-group-list">
        <label
          v-for="group in temuScopes"
          :key="`temu-${group.id}`"
          class="scope-group-item"
        >
          <input
            v-model="selectedTemuGroupIds"
            type="checkbox"
            :value="group.id"
          />
          <span
            ><strong>{{ group.name }}</strong
            ><small>
              共 {{ group.product_count }}，图片就绪
              {{ group.ready_product_count }}，已处理
              {{ group.processed_product_count }}，待处理
              {{ group.pending_product_count }}
            </small></span
          >
        </label>
        <p v-if="!temuScopes.length" class="empty">该用户暂无 Temu 分组</p>
      </div>
      <p class="form-help">
        当前范围：{{ scopeDescription }}。图片就绪成员约
        {{ selectedReadyCount }} 个，跨分组商品只处理一次。
      </p>
    </section>

    <section class="settings-panel">
      <div class="settings-list">
        <div class="section-heading">
          <h3>模型版本</h3>
          <button @click="createNew">新增版本</button>
        </div>
        <button
          v-for="config in configs"
          :key="config.id"
          class="config-item"
          :class="{ active: form.id === config.id }"
          @click="selectConfig(config)"
        >
          <span
            ><strong>{{ config.name }}</strong
            ><small
              >{{ config.model_name }} · {{ config.output_dimension }} 维</small
            ></span
          >
          <b v-if="config.is_active">使用中</b>
        </button>
      </div>

      <form class="ai-config-form" @submit.prevent="saveConfig">
        <p class="wide form-help">
          服务使用 Gemini embedContent 图片向量格式。模型 URL、API Key
          和模型名称均在此处保存，无需在 .env 中填写 AI_SECRET_KEY。
        </p>
        <label>配置名称<input v-model="form.name" required /></label>
        <label
          >自定义 Base URL<input
            v-model="form.base_url"
            type="url"
            placeholder="https://generativelanguage.googleapis.com/v1beta"
            required
        /></label>
        <p class="wide form-help">
          当前代理填写 https://newapi.momoaisite.com 即可，末尾 `/`
          可有可无；后端会自动生成
          `/v1beta/models/gemini-embedding-2:embedContent`。也可直接填写带
          `/v1beta` 的 Base URL 或完整 embedContent 地址。不要填写
          `/v1`，除非代理服务明确要求该版本。
        </p>
        <label
          >第三方 API Key<input
            v-model="form.api_key"
            type="password"
            :placeholder="
              form.id ? '留空则保持已保存密钥' : '在此填写第三方 API Key'
            "
        /></label>
        <label>模型名称<input v-model="form.model_name" required /></label>
        <label
          >输出维度<input
            v-model.number="form.output_dimension"
            type="number"
            min="1"
            max="1536"
            required
        /></label>
        <label>响应路径<input v-model="form.response_path" required /></label>
        <label
          >超时秒数<input
            v-model.number="form.timeout_seconds"
            type="number"
            min="5"
            max="600"
        /></label>
        <label
          >向量 TopK<input
            v-model.number="form.top_k"
            type="number"
            min="1"
            max="500"
        /></label>
        <label
          >pHash 自动阈值<input
            v-model.number="form.phash_auto_distance"
            type="number"
            min="0"
            max="64"
        /></label>
        <label
          >审核阈值<input
            v-model.number="form.review_threshold"
            type="number"
            min="-1"
            max="1"
            step="0.001"
        /></label>
        <label
          >自动合并阈值<input
            v-model.number="form.auto_merge_threshold"
            type="number"
            min="-1"
            max="1"
            step="0.001"
        /></label>
        <p class="wide form-help request-format-note">
          请求体由后台按 Gemini embedContent 图片格式自动生成，包括
          content.parts.inline_data 和 output_dimensionality，无需手工填写
          JSON。
        </p>
        <label class="switch-field"
          ><input
            v-model="form.auto_merge_enabled"
            type="checkbox"
          />启用严格阈值自动合并</label
        >
        <label class="switch-field"
          ><input
            v-model="form.activate"
            type="checkbox"
          />保存后设为当前模型</label
        >
        <p class="wide form-help">
          保存或启用模型只更新配置，不会把历史商品自动加入队列。历史商品必须使用下方“手动启动向量化”；新采集商品是否自动入队由顶部开关控制。
        </p>
        <div class="wide config-actions">
          <button :disabled="saving">
            {{ saving ? "保存中..." : "保存配置" }}
          </button>
          <button
            v-if="form.id"
            type="button"
            @click="enqueueSelectedScope(form)"
          >
            手动启动向量化
          </button>
          <button
            v-if="form.id"
            type="button"
            class="secondary"
            @click="rebuild(form)"
          >
            重新处理当前范围
          </button>
          <button
            v-if="form.id"
            type="button"
            class="secondary"
            :disabled="testing"
            @click="testConfig(form)"
          >
            {{ testing ? "测试中..." : "测试接口" }}
          </button>
        </div>
        <p class="wide form-help">
          请求使用 content.parts.inline_data 传输 JPEG 图片，响应向量默认读取
          embedding.values。自定义代理的响应字段不同时，只需修改响应路径。
        </p>
        <p
          v-if="testResult"
          class="wide"
          :class="testResult.success ? 'test-success' : 'test-error'"
        >
          {{ testResult.message }}
        </p>
      </form>
    </section>

    <section class="review-panel failure-panel">
      <div class="section-heading">
        <h3>失败任务明细</h3>
        <div v-if="failedTasks.length" class="section-heading-actions">
          <button @click="retryAllFailed">全部失败重试</button>
          <button class="danger" @click="clearFailedTasks">清空失败记录</button>
        </div>
      </div>
      <div v-if="!failedTasks.length" class="empty review-empty">
        暂无失败任务
      </div>
      <article v-for="task in failedTasks" :key="task.id" class="failure-card">
        <div>
          <strong>商品 {{ task.offer_id }}</strong
          ><span>{{ task.config_name }} · 已尝试 {{ task.attempts }} 次</span>
        </div>
        <pre>{{ task.last_error || "无错误详情" }}</pre>
        <button @click="retryTask(task.id)">单项重试</button>
      </article>
    </section>

    <section class="review-panel">
      <div class="section-heading candidate-review-heading">
        <div>
          <h3>疑似同款审核</h3>
          <span
            >{{ candidates.length }} 组，已选择
            {{ selectedCandidateIds.length }} 组</span
          >
        </div>
        <div v-if="candidates.length" class="section-heading-actions">
          <button class="secondary" @click="toggleSelectAll">
            {{ allCandidatesSelected ? "取消全选" : "本页全选" }}
          </button>
          <button
            :disabled="!selectedCandidateIds.length || batchDeciding"
            @click="batchDecide('approve')"
          >
            批量确认同款
          </button>
          <button
            class="danger"
            :disabled="!selectedCandidateIds.length || batchDeciding"
            @click="batchDecide('reject')"
          >
            批量拒绝
          </button>
        </div>
      </div>
      <div v-if="!candidates.length" class="empty review-empty">
        暂无待审核候选
      </div>
      <article
        v-for="candidate in candidates"
        :key="`${candidate.candidate_type}-${candidate.id}`"
        class="candidate-card"
        :class="{
          selected: selectedCandidateIds.includes(
            `${candidate.candidate_type}:${candidate.id}`,
          ),
        }"
      >
        <label class="candidate-select" title="选择该候选">
          <input
            v-model="selectedCandidateIds"
            type="checkbox"
            :value="`${candidate.candidate_type}:${candidate.id}`"
          />
        </label>
        <div class="candidate-product">
          <img
            v-if="candidate.left.image_url"
            :src="candidate.left.image_url"
          />
          <strong
            >{{ candidate.left.platform }} ·
            {{ candidate.left.external_id }}</strong
          >
          <span
            >{{ candidate.left.metric_label }}
            {{ Number(candidate.left.metric_value).toLocaleString() }}</span
          >
          <small>{{
            candidate.left.sources
              .map((item) => `${item.username}/${item.group_name}`)
              .join("、")
          }}</small>
        </div>
        <div class="candidate-score">
          <strong>{{ formatScore(candidate.image_similarity) }}</strong>
          <span>{{
            candidate.candidate_type === "cross_platform"
              ? "跨平台货源匹配"
              : "平台同款"
          }}</span>
          <small>pHash 距离 {{ candidate.phash_distance ?? "-" }}</small>
          <div>
            <button @click="decide(candidate, 'approve')">确认关系</button>
            <button class="danger" @click="decide(candidate, 'reject')">
              拒绝
            </button>
          </div>
        </div>
        <div class="candidate-product">
          <img
            v-if="candidate.right.image_url"
            :src="candidate.right.image_url"
          />
          <strong
            >{{ candidate.right.platform }} ·
            {{ candidate.right.external_id }}</strong
          >
          <span
            >{{ candidate.right.metric_label }}
            {{ Number(candidate.right.metric_value).toLocaleString() }}</span
          >
          <small>{{
            candidate.right.sources
              .map((item) => `${item.username}/${item.group_name}`)
              .join("、")
          }}</small>
        </div>
      </article>
    </section>
  </section>
</template>
