<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { api, appendQuery } from "./api";
import PlatformHome from "./components/PlatformHome.vue";
import TemuManager from "./components/TemuManager.vue";

const currentUser = ref(null);
const loginForm = reactive({ username: "admin", password: "" });
const loginError = ref("");
const groups = ref([]);
const users = ref([]);
const selectedOwnerId = ref(null);
const selectedGroupId = ref(null);
const products = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = 30;
const loading = ref(false);
const detail = ref(null);
const newGroupName = ref("");
const sort = reactive({ field: "daily_orders", direction: "desc" });
const activeView = ref("products");
const createdToken = ref("");
const userForm = reactive({ username: "", password: "", role: "user" });
const filters = reactive(defaultFilters());
const selectedPlatform = ref(null);

function defaultFilters() {
  return {
    listing_date_from: "",
    listing_date_to: "",
    listed_days_min: "",
    listed_days_max: "",
    total_orders_min: "",
    total_orders_max: "",
    total_pieces_min: "",
    total_pieces_max: "",
    daily_orders_min: "",
    daily_orders_max: "",
    collected_at_from: "",
    collected_at_to: "",
  };
}

const isAdmin = computed(() => currentUser.value?.role === "admin");
const selectedGroup = computed(() =>
  groups.value.find((group) => group.id === selectedGroupId.value),
);
const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize)),
);

window.addEventListener("session-expired", () => {
  currentUser.value = null;
});

async function checkSession() {
  try {
    currentUser.value = await api("/api/auth/me");
    selectedOwnerId.value = currentUser.value.id;
    if (isAdmin.value) await loadUsers();
    await loadGroups();
  } catch {
    currentUser.value = null;
  }
}

async function login() {
  loginError.value = "";
  try {
    currentUser.value = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(loginForm),
    });
    loginForm.password = "";
    selectedOwnerId.value = currentUser.value.id;
    if (isAdmin.value) await loadUsers();
    await loadGroups();
  } catch (error) {
    loginError.value = error.message;
  }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => null);
  currentUser.value = null;
  selectedPlatform.value = null;
}

async function loadUsers() {
  users.value = await api("/api/admin/users");
}

async function changeOwner() {
  selectedGroupId.value = null;
  detail.value = null;
  await loadGroups();
}

async function loadGroups() {
  const params = new URLSearchParams();
  if (isAdmin.value && selectedOwnerId.value)
    params.set("owner_id", selectedOwnerId.value);
  groups.value = await api(`/api/groups?${params}`);
  if (!groups.value.length) {
    selectedGroupId.value = null;
    products.value = [];
    total.value = 0;
    return;
  }
  if (!groups.value.some((group) => group.id === selectedGroupId.value))
    selectGroup(groups.value[0]);
  else await loadProducts();
}

function selectGroup(group) {
  selectedGroupId.value = group.id;
  sort.field = group.default_sort_field;
  sort.direction = group.default_sort_direction;
  page.value = 1;
  detail.value = null;
  loadProducts();
}

async function createGroup() {
  const name = newGroupName.value.trim();
  if (!name) return;
  const params = isAdmin.value ? `?owner_id=${selectedOwnerId.value}` : "";
  const group = await api(`/api/groups${params}`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  newGroupName.value = "";
  await loadGroups();
  selectGroup(group);
}

async function renameGroup() {
  if (!selectedGroup.value) return;
  const name = window.prompt("新的分组名称", selectedGroup.value.name)?.trim();
  if (!name || name === selectedGroup.value.name) return;
  await api(`/api/groups/${selectedGroup.value.id}`, {
    method: "PUT",
    body: JSON.stringify({ name }),
  });
  await loadGroups();
}

async function deleteGroup() {
  if (
    !selectedGroup.value ||
    !window.confirm(
      `删除分组“${selectedGroup.value.name}”？商品和历史不会被删除。`,
    )
  )
    return;
  await api(`/api/groups/${selectedGroup.value.id}`, { method: "DELETE" });
  await loadGroups();
}

function validateFilters() {
  const pairs = [
    ["listing_date_from", "listing_date_to"],
    ["listed_days_min", "listed_days_max"],
    ["total_orders_min", "total_orders_max"],
    ["total_pieces_min", "total_pieces_max"],
    ["daily_orders_min", "daily_orders_max"],
    ["collected_at_from", "collected_at_to"],
  ];
  return pairs.every(
    ([min, max]) =>
      !filters[min] || !filters[max] || filters[min] <= filters[max],
  );
}

async function applyFilters() {
  if (!validateFilters()) return window.alert("筛选区间的最小值不能大于最大值");
  page.value = 1;
  await loadProducts();
}

async function resetFilters() {
  Object.assign(filters, defaultFilters());
  page.value = 1;
  await loadProducts();
}

async function loadProducts() {
  if (!selectedGroupId.value) return;
  loading.value = true;
  try {
    const params = new URLSearchParams({
      page: page.value,
      page_size: pageSize,
      sort_field: sort.field,
      sort_direction: sort.direction,
    });
    Object.entries(filters).forEach(([name, value]) =>
      appendQuery(
        params,
        name,
        value
          ? name.startsWith("collected_at_")
            ? new Date(value).toISOString()
            : value
          : "",
      ),
    );
    const result = await api(
      `/api/groups/${selectedGroupId.value}/products?${params}`,
    );
    products.value = result.items;
    total.value = result.total;
  } finally {
    loading.value = false;
  }
}

async function toggleSort(field) {
  sort.direction =
    sort.field === field && sort.direction === "desc" ? "asc" : "desc";
  sort.field = field;
  page.value = 1;
  await api(`/api/groups/${selectedGroupId.value}`, {
    method: "PUT",
    body: JSON.stringify({
      default_sort_field: sort.field,
      default_sort_direction: sort.direction,
    }),
  });
  await loadProducts();
}

function sortMark(field) {
  return sort.field === field ? (sort.direction === "asc" ? " ↑" : " ↓") : "";
}

async function showDetail(product) {
  const ownerParam = isAdmin.value ? `?owner_id=${selectedOwnerId.value}` : "";
  detail.value = await api(`/api/products/${product.id}${ownerParam}`);
}

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || value === "") return "-";
  return Number(value).toLocaleString("zh-CN", {
    maximumFractionDigits: digits,
  });
}

async function createManagedUser() {
  createdToken.value = "";
  const created = await api("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(userForm),
  });
  createdToken.value = created.ingest_token;
  Object.assign(userForm, { username: "", password: "", role: "user" });
  await loadUsers();
}

async function toggleUser(user) {
  await api(`/api/admin/users/${user.id}/status`, {
    method: "PUT",
    body: JSON.stringify({ is_active: !user.is_active }),
  });
  await loadUsers();
}

async function resetPassword(user) {
  const password = window.prompt(`输入 ${user.username} 的新密码`)?.trim();
  if (!password) return;
  await api(`/api/admin/users/${user.id}/reset-password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  window.alert("密码已重置");
}

async function resetToken(user) {
  if (!window.confirm(`重置 ${user.username} 的采集令牌？旧令牌将立即失效。`))
    return;
  const result = await api(`/api/admin/users/${user.id}/reset-ingest-token`, {
    method: "POST",
  });
  createdToken.value = result.ingest_token;
  await loadUsers();
}

watch(page, loadProducts);
onMounted(checkSession);
</script>

<template>
  <main v-if="!currentUser" class="login-page">
    <form class="login-card" @submit.prevent="login">
      <h1>产品数据管理</h1>
      <input
        v-model="loginForm.username"
        autocomplete="username"
        placeholder="用户账号"
      />
      <input
        v-model="loginForm.password"
        autocomplete="current-password"
        type="password"
        placeholder="密码"
      />
      <button>登录</button>
      <p v-if="loginError" class="error">{{ loginError }}</p>
    </form>
  </main>

  <PlatformHome
    v-else-if="!selectedPlatform"
    :user="currentUser"
    @select="selectedPlatform = $event"
    @logout="logout"
  />

  <TemuManager
    v-else-if="selectedPlatform === 'temu'"
    :user="currentUser"
    :users="users"
    @home="selectedPlatform = null"
    @logout="logout"
  />

  <div v-else class="app-shell">
    <aside>
      <button class="back-home" @click="selectedPlatform = null">
        ← 平台首页
      </button>
      <h1>1688 商品</h1>
      <p class="identity">
        {{ currentUser.username }} · {{ isAdmin ? "管理员" : "用户" }}
      </p>
      <div v-if="isAdmin" class="view-tabs">
        <button
          :class="{ active: activeView === 'products' }"
          @click="activeView = 'products'"
        >
          商品
        </button>
        <button
          :class="{ active: activeView === 'users' }"
          @click="activeView = 'users'"
        >
          用户
        </button>
      </div>
      <template v-if="activeView === 'products'">
        <label v-if="isAdmin" class="owner-select"
          >查看用户
          <select v-model="selectedOwnerId" @change="changeOwner">
            <option v-for="user in users" :key="user.id" :value="user.id">
              {{ user.username }}
            </option>
          </select>
        </label>
        <div class="new-group">
          <input
            v-model="newGroupName"
            placeholder="新分组名称"
            @keyup.enter="createGroup"
          /><button @click="createGroup">新增</button>
        </div>
        <nav>
          <button
            v-for="group in groups"
            :key="group.id"
            :class="{ active: group.id === selectedGroupId }"
            @click="selectGroup(group)"
          >
            {{ group.name }}
          </button>
        </nav>
      </template>
      <button class="logout" @click="logout">退出登录</button>
    </aside>

    <section v-if="activeView === 'products'" class="content">
      <header>
        <div>
          <h2>{{ selectedGroup?.name || "请先创建分组" }}</h2>
          <span v-if="selectedGroup">共 {{ total }} 个商品</span>
        </div>
        <div v-if="selectedGroup" class="group-actions">
          <button @click="renameGroup">重命名</button
          ><button class="danger" @click="deleteGroup">删除分组</button>
        </div>
      </header>
      <form v-if="selectedGroup" class="filters" @submit.prevent="applyFilters">
        <label
          >上架日期<input
            v-model="filters.listing_date_from"
            type="date" /><span>至</span
          ><input v-model="filters.listing_date_to" type="date"
        /></label>
        <label
          >上架天数<input
            v-model="filters.listed_days_min"
            type="number"
            min="0"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.listed_days_max"
            type="number"
            min="0"
            placeholder="最大"
        /></label>
        <label
          >总笔数<input
            v-model="filters.total_orders_min"
            type="number"
            min="0"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.total_orders_max"
            type="number"
            min="0"
            placeholder="最大"
        /></label>
        <label
          >总件数<input
            v-model="filters.total_pieces_min"
            type="number"
            min="0"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.total_pieces_max"
            type="number"
            min="0"
            placeholder="最大"
        /></label>
        <label
          >每日笔数<input
            v-model="filters.daily_orders_min"
            type="number"
            min="0"
            step="0.0001"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.daily_orders_max"
            type="number"
            min="0"
            step="0.0001"
            placeholder="最大"
        /></label>
        <label
          >采集时间<input
            v-model="filters.collected_at_from"
            type="datetime-local" /><span>至</span
          ><input v-model="filters.collected_at_to" type="datetime-local"
        /></label>
        <div class="filter-actions">
          <button type="submit">应用筛选</button
          ><button type="button" class="secondary" @click="resetFilters">
            重置
          </button>
        </div>
      </form>
      <div v-if="selectedGroup" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>图片</th>
              <th @click="toggleSort('offer_id')">
                商品 ID{{ sortMark("offer_id") }}
              </th>
              <th @click="toggleSort('listing_date')">
                上架日期{{ sortMark("listing_date") }}
              </th>
              <th @click="toggleSort('listed_days')">
                天数{{ sortMark("listed_days") }}
              </th>
              <th @click="toggleSort('total_orders')">
                总笔数{{ sortMark("total_orders") }}
              </th>
              <th @click="toggleSort('total_pieces')">
                总件数{{ sortMark("total_pieces") }}
              </th>
              <th @click="toggleSort('daily_orders')">
                每日笔数{{ sortMark("daily_orders") }}
              </th>
              <th @click="toggleSort('last_collected_at')">
                采集时间{{ sortMark("last_collected_at") }}
              </th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="product in products" :key="product.id">
              <td>
                <img
                  v-if="product.image_url"
                  :src="product.image_url"
                  loading="lazy"
                  class="product-image"
                />
                <div v-else class="image-placeholder">暂无图片</div>
              </td>
              <td>
                <a
                  :href="product.product_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  >{{ product.offer_id }}</a
                >
              </td>
              <td>{{ product.listing_date }}</td>
              <td>{{ product.listed_days }}</td>
              <td>{{ formatNumber(product.total_orders, 0) }}</td>
              <td>{{ formatNumber(product.total_pieces, 0) }}</td>
              <td>{{ formatNumber(product.daily_orders) }}</td>
              <td>
                {{
                  new Date(product.last_collected_at).toLocaleString("zh-CN")
                }}
              </td>
              <td><button @click="showDetail(product)">详情</button></td>
            </tr>
            <tr v-if="!loading && !products.length">
              <td colspan="9" class="empty">没有符合条件的商品</td>
            </tr>
          </tbody>
        </table>
        <div v-if="loading" class="loading">加载中...</div>
      </div>
      <div v-if="selectedGroup" class="pagination">
        <button :disabled="page <= 1" @click="page--">上一页</button
        ><span>第 {{ page }} / {{ totalPages }} 页</span
        ><button :disabled="page >= totalPages" @click="page++">下一页</button>
      </div>
    </section>

    <section v-else class="content user-management">
      <header>
        <div>
          <h2>用户管理</h2>
          <span>管理员创建、禁用用户并重置个人采集令牌</span>
        </div>
      </header>
      <form class="user-create" @submit.prevent="createManagedUser">
        <input
          v-model="userForm.username"
          placeholder="用户名"
          required
        /><input
          v-model="userForm.password"
          type="password"
          minlength="8"
          placeholder="初始密码"
          required
        /><select v-model="userForm.role">
          <option value="user">普通用户</option>
          <option value="admin">管理员</option></select
        ><button>创建用户</button>
      </form>
      <div v-if="createdToken" class="token-box">
        <strong>新采集令牌（仅本次显示）</strong><code>{{ createdToken }}</code
        ><button @click="navigator.clipboard.writeText(createdToken)">
          复制
        </button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>用户名</th>
              <th>角色</th>
              <th>状态</th>
              <th>令牌标识</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td>{{ user.username }}</td>
              <td>{{ user.role }}</td>
              <td>{{ user.is_active ? "启用" : "禁用" }}</td>
              <td>{{ user.ingest_token_prefix || "未生成" }}</td>
              <td>{{ new Date(user.created_at).toLocaleString("zh-CN") }}</td>
              <td class="row-actions">
                <button @click="toggleUser(user)">
                  {{ user.is_active ? "禁用" : "启用" }}</button
                ><button @click="resetPassword(user)">重置密码</button
                ><button @click="resetToken(user)">重置令牌</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div v-if="detail" class="drawer-backdrop" @click.self="detail = null">
      <section class="drawer">
        <button class="close" @click="detail = null">×</button
        ><img
          v-if="detail.image_url"
          :src="detail.image_url"
          class="detail-image"
        />
        <h2>商品 {{ detail.offer_id }}</h2>
        <dl>
          <dt>上架日期</dt>
          <dd>{{ detail.listing_date }}</dd>
          <dt>上架天数</dt>
          <dd>{{ detail.listed_days }}</dd>
          <dt>总笔数</dt>
          <dd>{{ formatNumber(detail.total_orders, 0) }}</dd>
          <dt>总件数</dt>
          <dd>{{ formatNumber(detail.total_pieces, 0) }}</dd>
          <dt>平均每笔件数</dt>
          <dd>{{ formatNumber(detail.average_pieces_per_order) }}</dd>
          <dt>计算天数</dt>
          <dd>{{ detail.calculation_days }}</dd>
          <dt>每日笔数</dt>
          <dd>{{ formatNumber(detail.daily_orders) }}</dd>
          <dt>所属分组</dt>
          <dd>{{ detail.groups.map((group) => group.name).join("、") }}</dd>
        </dl>
        <h3>当前用户采集历史</h3>
        <table class="history">
          <thead>
            <tr>
              <th>时间</th>
              <th>天数</th>
              <th>笔数</th>
              <th>件数</th>
              <th>每日笔数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="snapshot in detail.snapshots" :key="snapshot.id">
              <td>
                {{ new Date(snapshot.collected_at).toLocaleString("zh-CN") }}
              </td>
              <td>{{ snapshot.listed_days }}</td>
              <td>{{ snapshot.total_orders }}</td>
              <td>{{ snapshot.total_pieces }}</td>
              <td>{{ formatNumber(snapshot.daily_orders) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>
