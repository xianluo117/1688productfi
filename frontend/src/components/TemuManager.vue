<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { api, appendQuery } from "../api";

const props = defineProps({
  user: { type: Object, required: true },
  users: { type: Array, default: () => [] },
});
const emit = defineEmits(["home", "logout"]);

const isAdmin = computed(() => props.user.role === "admin");
const selectedOwnerId = ref(props.user.id);
const groups = ref([]);
const selectedGroupId = ref(null);
const products = ref([]);
const detail = ref(null);
const total = ref(0);
const page = ref(1);
const pageSize = 30;
const loading = ref(false);
const newGroupName = ref("");
const sort = reactive({ field: "daily_sales", direction: "desc" });
const filters = reactive({
  search: "",
  daily_sales_min: "",
  daily_sales_max: "",
  weekly_sales_min: "",
  weekly_sales_max: "",
  total_sales_min: "",
  total_sales_max: "",
  sale_price_min: "",
  sale_price_max: "",
  listing_date_from: "",
  listing_date_to: "",
  listed_days_min: "",
  listed_days_max: "",
  shop_type: "",
  include_ads: false,
});

const selectedGroup = computed(() =>
  groups.value.find((group) => group.id === selectedGroupId.value),
);
const totalPages = computed(() =>
  Math.max(1, Math.ceil(total.value / pageSize)),
);

async function loadGroups() {
  const params = new URLSearchParams();
  if (isAdmin.value) params.set("owner_id", selectedOwnerId.value);
  groups.value = await api(`/api/temu/groups?${params}`);
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
  const owner = isAdmin.value ? `?owner_id=${selectedOwnerId.value}` : "";
  const group = await api(`/api/temu/groups${owner}`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  newGroupName.value = "";
  await loadGroups();
  selectGroup(group);
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
      include_ads: filters.include_ads,
    });
    Object.entries(filters).forEach(([name, value]) => {
      if (name !== "include_ads") appendQuery(params, name, value);
    });
    const result = await api(
      `/api/temu/groups/${selectedGroupId.value}/products?${params}`,
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
  Object.assign(filters, {
    search: "",
    daily_sales_min: "",
    daily_sales_max: "",
    weekly_sales_min: "",
    weekly_sales_max: "",
    total_sales_min: "",
    total_sales_max: "",
    sale_price_min: "",
    sale_price_max: "",
    listing_date_from: "",
    listing_date_to: "",
    listed_days_min: "",
    listed_days_max: "",
    shop_type: "",
    include_ads: false,
  });
  page.value = 1;
  await loadProducts();
}

async function toggleSort(field) {
  sort.direction =
    sort.field === field && sort.direction === "desc" ? "asc" : "desc";
  sort.field = field;
  page.value = 1;
  await api(`/api/temu/groups/${selectedGroupId.value}`, {
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

function number(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  return Number(value).toLocaleString("zh-CN", {
    maximumFractionDigits: digits,
  });
}

async function showDetail(product) {
  const owner = isAdmin.value ? `?owner_id=${selectedOwnerId.value}` : "";
  detail.value = await api(`/api/temu/products/${product.id}${owner}`);
}

watch(page, loadProducts);
onMounted(loadGroups);
</script>

<template>
  <div class="app-shell temu-shell">
    <aside>
      <button class="back-home" @click="emit('home')">← 平台首页</button>
      <h1>Temu 产品</h1>
      <p class="identity">
        {{ user.username }} · {{ isAdmin ? "管理员" : "用户" }}
      </p>
      <label v-if="isAdmin" class="owner-select"
        >查看用户
        <select v-model="selectedOwnerId" @change="loadGroups">
          <option v-for="item in users" :key="item.id" :value="item.id">
            {{ item.username }}
          </option>
        </select>
      </label>
      <div class="new-group">
        <input
          v-model="newGroupName"
          placeholder="新 Temu 分组"
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
          <h2>{{ selectedGroup?.name || "请先创建 Temu 分组" }}</h2>
          <span v-if="selectedGroup">共 {{ total }} 个商品</span>
        </div>
      </header>
      <form
        v-if="selectedGroup"
        class="filters temu-filters"
        @submit.prevent="applyFilters"
      >
        <label class="single"
          >搜索<input v-model="filters.search" placeholder="商品 ID 或标题"
        /></label>
        <label
          >日销量<input
            v-model="filters.daily_sales_min"
            type="number"
            min="0"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.daily_sales_max"
            type="number"
            min="0"
            placeholder="最大"
        /></label>
        <label
          >周销量<input
            v-model="filters.weekly_sales_min"
            type="number"
            min="0"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.weekly_sales_max"
            type="number"
            min="0"
            placeholder="最大"
        /></label>
        <label
          >总销量<input
            v-model="filters.total_sales_min"
            type="number"
            min="0"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.total_sales_max"
            type="number"
            min="0"
            placeholder="最大"
        /></label>
        <label
          >售价<input
            v-model="filters.sale_price_min"
            type="number"
            min="0"
            step="0.01"
            placeholder="最小" /><span>至</span
          ><input
            v-model="filters.sale_price_max"
            type="number"
            min="0"
            step="0.01"
            placeholder="最大"
        /></label>
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
        <label class="single"
          >店铺类型<select v-model="filters.shop_type">
            <option value="">全部</option>
            <option>半托Y1</option>
            <option>半托Y2</option>
            <option>全托管</option>
          </select></label
        >
        <label class="switch-field"
          ><input
            v-model="filters.include_ads"
            type="checkbox"
          />显示广告产品</label
        >
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
              <th>主图</th>
              <th @click="toggleSort('temu_product_id')">
                商品 ID{{ sortMark("temu_product_id") }}
              </th>
              <th>标题</th>
              <th @click="toggleSort('sale_price')">
                售价{{ sortMark("sale_price") }}
              </th>
              <th>原价</th>
              <th @click="toggleSort('daily_sales')">
                日销量{{ sortMark("daily_sales") }}
              </th>
              <th @click="toggleSort('weekly_sales')">
                周销量{{ sortMark("weekly_sales") }}
              </th>
              <th @click="toggleSort('total_sales')">
                总销量{{ sortMark("total_sales") }}
              </th>
              <th @click="toggleSort('listing_date')">
                上架时间{{ sortMark("listing_date") }}
              </th>
              <th>上架天数</th>
              <th>店铺类型</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="product in products" :key="product.id">
              <td>
                <img
                  v-if="product.image_url"
                  :src="product.image_url"
                  class="product-image"
                  loading="lazy"
                />
                <div v-else class="image-placeholder">暂无图片</div>
              </td>
              <td>
                <a :href="product.product_url" target="_blank">{{
                  product.temu_product_id
                }}</a>
              </td>
              <td class="title-cell">{{ product.title }}</td>
              <td>${{ number(product.sale_price) }}</td>
              <td>${{ number(product.original_price) }}</td>
              <td>{{ number(product.daily_sales, 0) }}</td>
              <td>{{ number(product.weekly_sales, 0) }}</td>
              <td>{{ number(product.total_sales, 0) }}</td>
              <td>{{ product.listing_date || "-" }}</td>
              <td>{{ product.listed_days ?? "-" }}</td>
              <td>
                <span class="shop-type-badge">{{
                  product.derived_shop_type || "-"
                }}</span
                ><span v-if="product.is_ad" class="ad-badge">广告</span>
              </td>
              <td><button @click="showDetail(product)">详情</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="loading" class="loading">正在加载</p>
        <p v-else-if="!products.length" class="empty">
          暂无符合条件的 Temu 商品
        </p>
      </div>
      <div v-if="selectedGroup" class="pagination">
        <button :disabled="page <= 1" @click="page--">上一页</button
        ><span>{{ page }} / {{ totalPages }}</span
        ><button :disabled="page >= totalPages" @click="page++">下一页</button>
      </div>

      <div v-if="detail" class="modal-backdrop" @click.self="detail = null">
        <article class="detail-card temu-detail">
          <button class="modal-close" @click="detail = null">×</button>
          <h2>{{ detail.title }}</h2>
          <div class="detail-grid">
            <span>日/周/月/总销量</span
            ><strong
              >{{ number(detail.daily_sales, 0) }} /
              {{ number(detail.weekly_sales, 0) }} /
              {{ number(detail.monthly_sales, 0) }} /
              {{ number(detail.total_sales, 0) }}</strong
            ><span>日/周/月/总销售额</span
            ><strong
              >{{ number(detail.daily_revenue) }} /
              {{ number(detail.weekly_revenue) }} /
              {{ number(detail.monthly_revenue) }} /
              {{ number(detail.total_revenue) }}</strong
            ><span>店铺</span
            ><strong
              >{{ detail.shop_name || "-" }} ·
              {{ detail.derived_shop_type || "-" }}</strong
            ><span>标签</span><strong>{{ detail.temu_tags || "-" }}</strong>
          </div>
          <h3>个人采集历史</h3>
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>日销量</th>
                <th>周销量</th>
                <th>总销量</th>
                <th>售价</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="snapshot in detail.snapshots" :key="snapshot.id">
                <td>{{ new Date(snapshot.collected_at).toLocaleString() }}</td>
                <td>{{ snapshot.daily_sales }}</td>
                <td>{{ snapshot.weekly_sales }}</td>
                <td>{{ snapshot.total_sales }}</td>
                <td>{{ snapshot.sale_price }}</td>
              </tr>
            </tbody>
          </table>
        </article>
      </div>
    </section>
  </div>
</template>
