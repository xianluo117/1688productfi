<script setup>
import { onMounted, onUnmounted, reactive, ref } from "vue";
import { api } from "../api";

const emit = defineEmits(["back"]);
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
});
const failedTasks = ref([]);
const testing = ref(false);
const testResult = ref(null);
const saving = ref(false);
let refreshTimer;
const openAiCompatibleTemplate = {
  model: "{{model}}",
  input: "{{image_data_url}}",
  dimensions: 1536,
};
const requestTemplateText = ref(
  JSON.stringify(openAiCompatibleTemplate, null, 2),
);
const form = reactive({
  id: null,
  name: "OpenAI 兼容图片向量模型",
  base_url: "",
  api_key: "",
  model_name: "",
  output_dimension: 1536,
  response_path: "data.0.embedding",
  timeout_seconds: 60,
  top_k: 100,
  phash_auto_distance: 4,
  review_threshold: 0.86,
  auto_merge_threshold: 0.94,
  auto_merge_enabled: false,
  activate: true,
});

async function load() {
  [configs.value, candidates.value, failedTasks.value] = await Promise.all([
    api("/api/similarity/admin/configs"),
    api("/api/similarity/admin/candidates?status=pending&limit=100"),
    api("/api/similarity/admin/tasks?status=failed&limit=100"),
  ]);
  Object.assign(stats, await api("/api/similarity/admin/stats"));
  if (!form.id && configs.value.length) selectConfig(configs.value[0]);
}

function selectConfig(config) {
  Object.assign(form, {
    ...config,
    api_key: "",
    activate: config.is_active,
  });
  requestTemplateText.value = JSON.stringify(config.request_template, null, 2);
}

function useOpenAiPreset() {
  requestTemplateText.value = JSON.stringify(openAiCompatibleTemplate, null, 2);
  form.response_path = "data.0.embedding";
}

function createNew() {
  Object.assign(form, {
    id: null,
    name: "OpenAI 兼容图片向量模型",
    base_url: "",
    api_key: "",
    model_name: "",
    output_dimension: 1536,
    response_path: "data.0.embedding",
    timeout_seconds: 60,
    top_k: 100,
    phash_auto_distance: 4,
    review_threshold: 0.86,
    auto_merge_threshold: 0.94,
    auto_merge_enabled: false,
    activate: true,
  });
  useOpenAiPreset();
}

async function saveConfig() {
  let requestTemplate;
  try {
    requestTemplate = JSON.parse(requestTemplateText.value);
  } catch {
    return window.alert("请求模板不是有效 JSON");
  }
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
        request_template: requestTemplate,
        api_key: form.api_key || null,
      }),
    });
    createNew();
    await load();
  } finally {
    saving.value = false;
  }
}

async function setConcurrency() {
  Object.assign(
    stats,
    await api("/api/similarity/admin/queue", {
      method: "PUT",
      body: JSON.stringify({ concurrency: stats.concurrency }),
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
  await api("/api/similarity/admin/tasks/retry-failed", { method: "POST" });
  await load();
}

async function testConfig(config) {
  testing.value = true;
  testResult.value = null;
  try {
    testResult.value = await api(
      `/api/similarity/admin/configs/${config.id}/test`,
      { method: "POST" },
    );
  } catch (error) {
    testResult.value = { success: false, message: error.message };
  } finally {
    testing.value = false;
  }
}

async function rebuild(config) {
  await api(`/api/similarity/admin/configs/${config.id}/rebuild`, {
    method: "POST",
  });
  await load();
}

async function decide(candidate, decision) {
  await api(`/api/similarity/admin/candidates/${candidate.id}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
  await load();
}

function formatScore(value) {
  return value === null || value === undefined ? "-" : Number(value).toFixed(4);
}

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
        <h2>AI 同款识别</h2>
        <span>管理员保存 OpenAI 兼容图片向量接口、API Key 与审核规则</span>
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
      <label
        >并发数<input
          v-model.number="stats.concurrency"
          type="number"
          min="1"
          max="32"
      /></label>
      <button @click="setConcurrency">保存并发</button>
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
          服务类型固定为 OpenAI 兼容格式。模型 URL、API Key
          和模型名称均在此处保存，无需在 .env 中填写 AI_SECRET_KEY。
        </p>
        <label>配置名称<input v-model="form.name" required /></label>
        <label
          >自定义 Base URL<input
            v-model="form.base_url"
            type="url"
            placeholder="https://newapi.momoaisite.com/v1"
            required
        /></label>
        <p class="wide form-help">
          可填写 Base URL（例如 https://newapi.momoaisite.com/v1）或完整的
          /embeddings 地址；填写 Base URL 时后端自动追加 /embeddings。
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
        <label class="wide"
          >请求 JSON 模板<textarea
            v-model="requestTemplateText"
            rows="9"
            spellcheck="false"
          />
        </label>
        <div class="wide config-actions">
          <button type="button" class="secondary" @click="useOpenAiPreset">
            恢复 OpenAI 兼容模板
          </button>
        </div>
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
        <div class="wide config-actions">
          <button :disabled="saving">
            {{ saving ? "保存中..." : "保存配置" }}
          </button>
          <button
            v-if="form.id"
            type="button"
            class="secondary"
            @click="rebuild(form)"
          >
            重新排队
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
          默认请求为 model + input，响应向量读取
          data.0.embedding。第三方接口格式不同时，可修改 JSON
          模板和响应路径。模板占位符：model、image_data_url、image_base64、image_url、product_id，使用双大括号包裹。
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
        <button v-if="failedTasks.length" @click="retryAllFailed">
          全部失败重试
        </button>
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
      <div class="section-heading">
        <h3>疑似同款审核</h3>
        <span>{{ candidates.length }} 组</span>
      </div>
      <div v-if="!candidates.length" class="empty review-empty">
        暂无待审核候选
      </div>
      <article
        v-for="candidate in candidates"
        :key="candidate.id"
        class="candidate-card"
      >
        <div class="candidate-product">
          <img
            v-if="candidate.left.image_url"
            :src="candidate.left.image_url"
          />
          <strong>{{ candidate.left.offer_id }}</strong>
          <span>总件数 {{ candidate.left.total_pieces.toLocaleString() }}</span>
          <small>{{
            candidate.left.sources
              .map((item) => `${item.username}/${item.group_name}`)
              .join("、")
          }}</small>
        </div>
        <div class="candidate-score">
          <strong>{{ formatScore(candidate.image_similarity) }}</strong>
          <span>图片相似度</span>
          <small>pHash 距离 {{ candidate.phash_distance ?? "-" }}</small>
          <div>
            <button @click="decide(candidate, 'approve')">确认同款</button
            ><button class="danger" @click="decide(candidate, 'reject')">
              拒绝
            </button>
          </div>
        </div>
        <div class="candidate-product">
          <img
            v-if="candidate.right.image_url"
            :src="candidate.right.image_url"
          />
          <strong>{{ candidate.right.offer_id }}</strong>
          <span
            >总件数 {{ candidate.right.total_pieces.toLocaleString() }}</span
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
