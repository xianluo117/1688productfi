<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { api, appendQuery } from "../api";

const props = defineProps({
  user: { type: Object, required: true },
  users: { type: Array, default: () => [] },
});
const emit = defineEmits(["home", "logout"]);
const groups = ref([]);
const selectedOwnerId = ref(props.user.id);
const selectedGroupId = ref(null);
const products = ref([]);
const total = ref(0);
const page = ref(1);
const pageSize = 30;
const loading = ref(false);
const detail = ref(null);
const newGroupName = ref("");
const sort = reactive({ field: "daily_orders", direction: "desc" });
const filters = reactive(defaultFilters());
const isAdmin = computed(() => props.user.role === "admin");
const selectedGroup = computed(() =>
  groups.value.find((group) => group.id === selectedGroupId.value),
);
const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize)),
);

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

async function changeOwner() {
  selectedGroupId.value = null;
  detail.value = null;
  await loadGroups();
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
        value && name.startsWith("collected_at_")
          ? new Date(value).toISOString()
          : value,
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

async function applyFilters() {
  page.value = 1;
  await loadProducts();
}

async function resetFilters() {
  Object.assign(filters, defaultFilters());
  page.value = 1;
  await loadProducts();
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

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || value === "") return "-";
  return Number(value).toLocaleString("zh-CN", {
    maximumFractionDigits: digits,
  });
}

async function showDetail(product) {
  if (product.cluster_id) {
    detail.value = await api(`/api/similarity/clusters/${product.cluster_id}`);
    detail.value.isCluster = true;
    return;
  }
  const ownerParam = isAdmin.value ? `?owner_id=${selectedOwnerId.value}` : "";
  detail.value = await api(`/api/products/${product.id}${ownerParam}`);
}

watch(page, loadProducts);
onMounted(loadGroups);
</script>

<template>
  <div class="app-shell">
    <aside>
      <button class="back-home" @click="emit('home')">← 返回首页</button>
      <h1>1688 商品</h1>
      <p class="identity">
        {{ user.username }} · {{ isAdmin ? "管理员" : "用户" }}
      </p>
      <label v-if="isAdmin" class="owner-select">
        查看用户
        <select v-model="selectedOwnerId" @change="changeOwner">
          <option v-for="item in users" :key="item.id" :value="item.id">
            {{ item.username }}
          </option>
        </select>
      </label>
      <div class="new-group">
        <input
          v-model="newGroupName"
          placeholder="新分组名称"
          @keyup.enter="createGroup"
        />
        <button @click="createGroup">新增</button>
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
      <button class="logout" @click="emit('logout')">退出登录</button>
    </aside>

    <section class="content">
      <header>
        <div>
          <h2>{{ selectedGroup?.name || "请先创建分组" }}</h2>
          <span v-if="selectedGroup">共 {{ total }} 个商品</span>
        </div>
        <div v-if="selectedGroup" class="group-actions">
          <button @click="renameGroup">重命名</button>
          <button class="danger" @click="deleteGroup">删除分组</button>
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
          <button type="submit">应用筛选</button>
          <button type="button" class="secondary" @click="resetFilters">
            重置
          </button>
        </div>
      </form>
      <div v-if="selectedGroup" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>图片</th>
              <th>同款</th>
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
                <button
                  v-if="product.cluster_id"
                  class="cluster-badge"
                  @click="showDetail(product)"
                >
                  {{ product.cluster_member_count }} 件同款</button
                ><span v-else class="single-badge">单品</span>
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
              <td colspan="10" class="empty">没有符合条件的商品</td>
            </tr>
          </tbody>
        </table>
        <div v-if="loading" class="loading">加载中...</div>
      </div>
      <div v-if="selectedGroup" class="pagination">
        <button :disabled="page <= 1" @click="page--">上一页</button>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <button :disabled="page >= totalPages" @click="page++">下一页</button>
      </div>
    </section>

    <div v-if="detail" class="drawer-backdrop" @click.self="detail = null">
      <section v-if="detail.isCluster" class="drawer cluster-drawer">
        <button class="close" @click="detail = null">×</button>
        <h2>同款商品 · {{ detail.members.length }} 件</h2>
        <article
          v-for="member in detail.members"
          :key="member.id"
          class="cluster-member"
          :class="{ representative: member.id === detail.representative.id }"
        >
          <img v-if="member.image_url" :src="member.image_url" />
          <div>
            <strong>{{ member.offer_id }}</strong
            ><span
              >总件数 {{ formatNumber(member.total_pieces, 0) }} · 每日笔数
              {{ formatNumber(member.daily_orders) }}</span
            ><small>{{
              member.sources
                .map((item) => `${item.username}/${item.group_name}`)
                .join("、")
            }}</small
            ><a
              :href="member.product_url"
              target="_blank"
              rel="noopener noreferrer"
              >打开 1688 商品</a
            >
          </div>
          <b v-if="member.id === detail.representative.id">代表商品</b>
        </article>
      </section>
      <section v-else class="drawer">
        <button class="close" @click="detail = null">×</button>
        <img
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
          <dt>每日笔数</dt>
          <dd>{{ formatNumber(detail.daily_orders) }}</dd>
          <dt>所属分组</dt>
          <dd>{{ detail.groups.map((group) => group.name).join("、") }}</dd>
        </dl>
      </section>
    </div>
  </div>
</template>
