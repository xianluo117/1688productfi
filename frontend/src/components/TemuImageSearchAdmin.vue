<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { api, appendQuery } from "../api";

const emit = defineEmits(["back"]);
const props = defineProps({ users: { type: Array, default: () => [] } });

const ownerId = ref(null);
const groups = ref([]);
const selectedGroupIds = ref([]);
const tasks = ref([]);
const statusFilter = ref("");
const sourceFilter = ref("");
const busy = ref(false);
const saving = ref(false);
const testing = ref(false);
const config = reactive({
  base_url: "",
  api_key: "",
  api_key_masked: "",
  api_key_configured: false,
  auto_search_enabled: false,
  timeout_seconds: 30,
  poll_interval_seconds: 2,
  task_timeout_seconds: 180,
});
const health = reactive({ status: "unknown", message: "尚未检测" });
const stats = reactive({
  total: 0,
  searchable: 0,
  succeeded_products: 0,
  waiting_products: 0,
  pending: 0,
  processing: 0,
  completed: 0,
  failed: 0,
});
let timer;

const selectedOwner = computed(() =>
  props.users.find((user) => user.id === ownerId.value),
);

function scopePayload() {
  return { owner_id: ownerId.value, group_ids: selectedGroupIds.value };
}

async function loadConfig() {
  const result = await api("/api/temu-image-search/admin/config");
  Object.assign(config, result, { api_key: "" });
}

async function saveConfig() {
  saving.value = true;
  try {
    const payload = {
      base_url: config.base_url,
      api_key: config.api_key || null,
      auto_search_enabled: config.auto_search_enabled,
      timeout_seconds: Number(config.timeout_seconds),
      poll_interval_seconds: Number(config.poll_interval_seconds),
      task_timeout_seconds: Number(config.task_timeout_seconds),
    };
    const result = await api("/api/temu-image-search/admin/config", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    Object.assign(config, result, { api_key: "" });
  } finally {
    saving.value = false;
  }
}

async function testHealth() {
  testing.value = true;
  try {
    Object.assign(health, await api("/api/temu-image-search/admin/health"));
  } finally {
    testing.value = false;
  }
}

async function loadGroups() {
  if (!ownerId.value) {
    groups.value = [];
    return;
  }
  groups.value = await api(
    `/api/temu-image-search/admin/groups?owner_id=${ownerId.value}`,
  );
  selectedGroupIds.value = selectedGroupIds.value.filter((id) =>
    groups.value.some((group) => group.id === id),
  );
}

async function loadStats() {
  if (!ownerId.value) return;
  Object.assign(
    stats,
    await api("/api/temu-image-search/admin/stats", {
      method: "POST",
      body: JSON.stringify(scopePayload()),
    }),
  );
}

async function loadTasks() {
  const query = appendQuery("/api/temu-image-search/admin/tasks", {
    owner_id: ownerId.value || undefined,
    status: statusFilter.value || undefined,
    source: sourceFilter.value || undefined,
    limit: 300,
  });
  tasks.value = await api(query);
}

async function refresh() {
  await Promise.all([loadStats(), loadTasks()]);
}

async function enqueue() {
  if (!ownerId.value) return;
  const scope = selectedGroupIds.value.length
    ? `${selectedGroupIds.value.length} 个分组`
    : `${selectedOwner.value?.username || "当前用户"}的全部 Temu 分组`;
  if (!window.confirm(`确认重新搜索 ${scope} 中所有图片已就绪的商品？`)) return;
  busy.value = true;
  try {
    Object.assign(
      stats,
      await api("/api/temu-image-search/admin/enqueue", {
        method: "POST",
        body: JSON.stringify(scopePayload()),
      }),
    );
    await loadTasks();
  } finally {
    busy.value = false;
  }
}

async function retryTask(task) {
  await api(`/api/temu-image-search/admin/tasks/${task.id}/retry`, {
    method: "POST",
  });
  await refresh();
}

async function retryFailed() {
  if (!window.confirm("确认重试当前用户的全部失败图搜任务？")) return;
  await api(
    `/api/temu-image-search/admin/tasks/retry-failed?owner_id=${ownerId.value}`,
    { method: "POST" },
  );
  await refresh();
}

function statusText(status) {
  return (
    {
      pending: "等待搜索",
      processing: "搜索中",
      completed: "成功",
      failed: "失败",
      cancelled: "已取消",
    }[status] || status
  );
}

function sourceText(source) {
  return source === "image_ready" ? "图片就绪自动创建" : "管理员手动补跑";
}

watch(ownerId, async () => {
  selectedGroupIds.value = [];
  await loadGroups();
  await refresh();
});
watch([statusFilter, sourceFilter], loadTasks);

onMounted(async () => {
  ownerId.value = props.users[0]?.id || null;
  await loadConfig();
  if (ownerId.value) {
    await loadGroups();
    await refresh();
  }
  timer = window.setInterval(refresh, 3000);
});
onUnmounted(() => window.clearInterval(timer));
</script>

<template>
  <main class="app-shell image-search-admin">
    <header class="topbar">
      <div>
        <button class="secondary" @click="emit('back')">← 返回控制台</button>
        <h1>1688 以图找图</h1>
        <p>
          Temu 图片就绪后自动搜索 1688，保存官方搜索页和排序最前的 3 条商品。
        </p>
      </div>
    </header>

    <section class="panel">
      <div class="section-heading">
        <div>
          <h2>图搜 API 配置</h2>
          <p>API Key 加密保存，不会返回到浏览器。密钥留空表示保留现有值。</p>
        </div>
        <div class="inline-actions">
          <button class="secondary" :disabled="testing" @click="testHealth">
            {{ testing ? "检测中..." : "检测服务" }}
          </button>
          <button :disabled="saving" @click="saveConfig">
            {{ saving ? "保存中..." : "保存配置" }}
          </button>
        </div>
      </div>
      <div class="form-grid">
        <label class="wide"
          >API 地址<input
            v-model.trim="config.base_url"
            placeholder="https://search.example.com"
        /></label>
        <label
          >API Key<input
            v-model.trim="config.api_key"
            type="password"
            :placeholder="config.api_key_masked || '输入 SEARCH_API_KEY'"
        /></label>
        <label class="switch-field"
          ><input
            v-model="config.auto_search_enabled"
            type="checkbox"
          />图片就绪后自动图搜</label
        >
        <label
          >HTTP 超时（秒）<input
            v-model.number="config.timeout_seconds"
            type="number"
            min="5"
            max="120"
        /></label>
        <label
          >轮询间隔（秒）<input
            v-model.number="config.poll_interval_seconds"
            type="number"
            min="1"
            max="10"
        /></label>
        <label
          >任务截止（秒）<input
            v-model.number="config.task_timeout_seconds"
            type="number"
            min="30"
            max="600"
        /></label>
      </div>
      <p class="health-line" :class="health.status">
        服务状态：{{ health.status }}
        <span v-if="health.worker_limit">
          · 并发 {{ health.worker_limit }}</span
        >
        <span
          v-if="
            health.cookie_available !== null &&
            health.cookie_available !== undefined
          "
        >
          · Cookie {{ health.cookie_available ? "可用" : "不可用" }}</span
        >
        <span v-if="health.message"> · {{ health.message }}</span>
      </p>
    </section>

    <section class="panel">
      <div class="section-heading">
        <div>
          <h2>补跑范围</h2>
          <p>
            选择用户及 Temu 分组。未选择分组时处理该用户全部图片已就绪商品。
          </p>
        </div>
        <button :disabled="busy || !ownerId" @click="enqueue">
          {{ busy ? "创建中..." : "重新执行以图找图" }}
        </button>
      </div>
      <div class="form-grid">
        <label
          >目标用户<select v-model.number="ownerId">
            <option v-for="user in users" :key="user.id" :value="user.id">
              {{ user.username }}
            </option>
          </select></label
        >
        <div class="wide checkbox-grid">
          <label v-for="group in groups" :key="group.id" class="checkbox-item">
            <input
              v-model="selectedGroupIds"
              type="checkbox"
              :value="group.id"
            />
            <span>{{ group.name }}</span>
          </label>
          <span v-if="!groups.length" class="muted">该用户暂无 Temu 分组</span>
        </div>
      </div>
    </section>

    <section class="stats-grid">
      <article>
        <strong>{{ stats.total }}</strong
        ><span>商品总数</span>
      </article>
      <article>
        <strong>{{ stats.searchable }}</strong
        ><span>可执行图搜</span>
      </article>
      <article>
        <strong>{{ stats.succeeded_products }}</strong
        ><span>已有结果</span>
      </article>
      <article>
        <strong>{{ stats.waiting_products }}</strong
        ><span>待补跑商品</span>
      </article>
      <article>
        <strong>{{ stats.pending }}</strong
        ><span>等待搜索</span>
      </article>
      <article>
        <strong>{{ stats.processing }}</strong
        ><span>搜索中</span>
      </article>
      <article>
        <strong>{{ stats.failed }}</strong
        ><span>失败任务</span>
      </article>
    </section>

    <section class="panel">
      <div class="section-heading">
        <div>
          <h2>实时图搜日志</h2>
          <p>每 3 秒刷新，保存远程任务 ID、错误码和完整错误信息。</p>
        </div>
        <button
          class="secondary"
          :disabled="!stats.failed"
          @click="retryFailed"
        >
          批量重试失败任务
        </button>
      </div>
      <div class="filter-row">
        <label
          >状态<select v-model="statusFilter">
            <option value="">全部</option>
            <option value="pending">等待搜索</option>
            <option value="processing">搜索中</option>
            <option value="completed">成功</option>
            <option value="failed">失败</option>
          </select></label
        >
        <label
          >来源<select v-model="sourceFilter">
            <option value="">全部</option>
            <option value="image_ready">图片就绪自动创建</option>
            <option value="manual">管理员手动补跑</option>
          </select></label
        >
        <button class="secondary" @click="refresh">立即刷新</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>Temu 商品</th>
              <th>用户/分组</th>
              <th>来源</th>
              <th>状态</th>
              <th>远程任务</th>
              <th>尝试</th>
              <th>错误</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td>#{{ task.id }}</td>
              <td>
                <b>{{ task.temu_product_id }}</b
                ><small>{{ task.title }}</small>
              </td>
              <td>
                {{ task.owner_name || "-" }} / {{ task.group_name || "-" }}
              </td>
              <td>{{ sourceText(task.source) }}</td>
              <td>
                <span class="status-badge" :class="task.status">{{
                  statusText(task.status)
                }}</span>
              </td>
              <td class="remote-task-cell">{{ task.remote_task_id || "-" }}</td>
              <td>{{ task.attempts }}</td>
              <td class="error-cell" :title="task.last_error || ''">
                <b v-if="task.last_error_code">{{ task.last_error_code }}</b
                >{{ task.last_error || "-" }}
              </td>
              <td>
                <button
                  v-if="task.status === 'failed'"
                  class="secondary"
                  @click="retryTask(task)"
                >
                  重试
                </button>
              </td>
            </tr>
            <tr v-if="!tasks.length">
              <td colspan="9" class="empty">暂无图搜任务</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>
