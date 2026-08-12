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
const stats = reactive({
  total: 0,
  ready: 0,
  missing: 0,
  waiting: 0,
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

async function loadGroups() {
  if (!ownerId.value) {
    groups.value = [];
    return;
  }
  groups.value = await api(
    `/api/temu-image-downloads/admin/groups?owner_id=${ownerId.value}`,
  );
  selectedGroupIds.value = selectedGroupIds.value.filter((id) =>
    groups.value.some((group) => group.id === id),
  );
}

async function loadStats() {
  if (!ownerId.value) return;
  Object.assign(
    stats,
    await api("/api/temu-image-downloads/admin/stats", {
      method: "POST",
      body: JSON.stringify(scopePayload()),
    }),
  );
}

async function loadTasks() {
  const query = appendQuery("/api/temu-image-downloads/admin/tasks", {
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
  if (!window.confirm(`确认补齐 ${scope} 的未就绪图片？已就绪图片会跳过。`))
    return;
  busy.value = true;
  try {
    Object.assign(
      stats,
      await api("/api/temu-image-downloads/admin/enqueue", {
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
  await api(`/api/temu-image-downloads/admin/tasks/${task.id}/retry`, {
    method: "POST",
  });
  await refresh();
}

async function retryFailed() {
  if (!window.confirm("确认重试当前用户的全部失败图片下载任务？")) return;
  await api(
    `/api/temu-image-downloads/admin/tasks/retry-failed?owner_id=${ownerId.value}`,
    { method: "POST" },
  );
  await refresh();
}

function statusText(status) {
  return (
    {
      pending: "等待下载",
      processing: "下载中",
      completed: "成功",
      failed: "失败",
      cancelled: "已取消",
    }[status] || status
  );
}

function sourceText(source) {
  return source === "ingest" ? "采集自动创建" : "管理员手动补齐";
}

watch(ownerId, async () => {
  selectedGroupIds.value = [];
  await loadGroups();
  await refresh();
});
watch([statusFilter, sourceFilter], loadTasks);

onMounted(async () => {
  ownerId.value = props.users[0]?.id || null;
  if (ownerId.value) {
    await loadGroups();
    await refresh();
  }
  timer = window.setInterval(refresh, 3000);
});
onUnmounted(() => window.clearInterval(timer));
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <div>
        <button class="secondary" @click="emit('back')">← 返回控制台</button>
        <h1>Temu 图片下载</h1>
        <p>下载任务持久化保存，服务重启后仍会继续处理。</p>
      </div>
    </header>

    <section class="panel">
      <div class="section-heading">
        <div>
          <h2>下载范围</h2>
          <p>选择用户及 Temu 分组。未选择分组时处理该用户全部 Temu 商品。</p>
        </div>
        <button :disabled="busy || !ownerId" @click="enqueue">
          {{ busy ? "创建中..." : "补齐未就绪图片" }}
        </button>
      </div>
      <div class="form-grid">
        <label
          >目标用户
          <select v-model.number="ownerId">
            <option v-for="user in users" :key="user.id" :value="user.id">
              {{ user.username }}
            </option>
          </select>
        </label>
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
        <strong>{{ stats.ready }}</strong
        ><span>图片已就绪</span>
      </article>
      <article>
        <strong>{{ stats.waiting }}</strong
        ><span>待补齐</span>
      </article>
      <article>
        <strong>{{ stats.pending }}</strong
        ><span>等待下载</span>
      </article>
      <article>
        <strong>{{ stats.processing }}</strong
        ><span>下载中</span>
      </article>
      <article>
        <strong>{{ stats.failed }}</strong
        ><span>失败任务</span>
      </article>
    </section>

    <section class="panel">
      <div class="section-heading">
        <div>
          <h2>实时下载日志</h2>
          <p>每 3 秒刷新。错误详情由下载 worker 持久化记录。</p>
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
          >状态
          <select v-model="statusFilter">
            <option value="">全部</option>
            <option value="pending">等待下载</option>
            <option value="processing">下载中</option>
            <option value="completed">成功</option>
            <option value="failed">失败</option>
          </select>
        </label>
        <label
          >来源
          <select v-model="sourceFilter">
            <option value="">全部</option>
            <option value="ingest">采集自动创建</option>
            <option value="manual">管理员手动补齐</option>
          </select>
        </label>
        <button class="secondary" @click="refresh">立即刷新</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>商品</th>
              <th>用户/分组</th>
              <th>来源</th>
              <th>状态</th>
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
              <td>{{ task.attempts }}</td>
              <td class="error-cell" :title="task.last_error || ''">
                {{ task.last_error || "-" }}
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
              <td colspan="8" class="empty">暂无下载任务</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>
