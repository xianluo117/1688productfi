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
const detailLoading = ref(false);
const detailError = ref("");
const total = ref(0);
const page = ref(1);
const pageSize = 30;
const loading = ref(false);
const newGroupName = ref("");
const sort = reactive({ field: "daily_sales", direction: "desc" });
const showMoreImageSearch = ref(false);
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
  showMoreImageSearch.value = false;
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
  detailLoading.value = true;
  detailError.value = "";
  try {
    const owner = isAdmin.value ? `?owner_id=${selectedOwnerId.value}` : "";
    detail.value = await api(`/api/temu/products/${product.id}${owner}`);
    showMoreImageSearch.value = false;
  } catch (error) {
    detailError.value = error.message;
    window.alert(`加载 Temu 商品详情失败：${error.message}`);
  } finally {
    detailLoading.value = false;
  }
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
              <th>平台同款</th>
              <th>以图找图结果</th>
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
                <span v-if="product.cluster_id" class="cluster-badge"
                  >{{ product.cluster_member_count }} 件</span
                >
                <span v-else class="single-badge">单品</span>
              </td>
              <td class="image-search-top1-cell">
                <a
                  v-if="product.image_search?.top1?.image_url"
                  :href="product.image_search.top1.product_url || product.image_search.search_page_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  :title="product.image_search.top1.title || '打开 1688 图搜结果'"
                >
                  <img
                    :src="product.image_search.top1.image_url"
                    class="product-image image-search-image"
                    loading="lazy"
                  />
                </a>
                <span
                  v-else-if="['pending', 'processing'].includes(product.image_search?.status)"
                  class="status-badge processing"
                >搜索中</span>
                <span v-else-if="product.image_search?.status === 'failed'" class="status-badge failed">失败</span>
                <span v-else class="muted">暂无结果</span>
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
              <td>
                <button :disabled="detailLoading" @click="showDetail(product)">
                  {{ detailLoading ? "加载中" : "详情" }}
                </button>
              </td>
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

      <div v-if="detail" class="drawer-backdrop" @click.self="detail = null">
        <article class="drawer temu-detail">
          <button class="close" @click="detail = null">×</button>
          <div class="temu-detail-header">
            <img
              v-if="detail.image_url"
              :src="detail.image_url"
              class="temu-detail-image"
            />
            <div>
              <h2>{{ detail.title }}</h2>
              <a
                :href="detail.product_url"
                target="_blank"
                rel="noopener noreferrer"
              >
                打开 Temu 商品 {{ detail.temu_product_id }}
              </a>
            </div>
          </div>
          <div class="detail-grid">
            <span>售价 / 原价</span>
            <strong
              >${{ number(detail.sale_price) }} / ${{
                number(detail.original_price)
              }}</strong
            >
            <span>日/周/月/总销量</span>
            <strong>
              {{ number(detail.daily_sales, 0) }} /
              {{ number(detail.weekly_sales, 0) }} /
              {{ number(detail.monthly_sales, 0) }} /
              {{ number(detail.total_sales, 0) }}
            </strong>
            <span>日/周/月/总销售额</span>
            <strong>
              {{ number(detail.daily_revenue) }} /
              {{ number(detail.weekly_revenue) }} /
              {{ number(detail.monthly_revenue) }} /
              {{ number(detail.total_revenue) }}
            </strong>
            <span>上架日期 / 天数</span>
            <strong
              >{{ detail.listing_date || "-" }} /
              {{ detail.listed_days ?? "-" }}</strong
            >
            <span>店铺</span>
            <strong
              >{{ detail.shop_name || "-" }} ·
              {{ detail.derived_shop_type || "-" }}</strong
            >
            <span>所属分组</span>
            <strong>{{
              detail.groups.map((group) => group.name).join("、") || "-"
            }}</strong>
            <span>标签</span>
            <strong>{{ detail.temu_tags || "-" }}</strong>
          </div>
          <section class="image-search-detail-section">
            <div class="section-heading">
              <div>
                <h3>以图找图结果 · 1688</h3>
                <p v-if="detail.image_search?.found !== null && detail.image_search?.found !== undefined">
                  共找到 {{ number(detail.image_search.found, 0) }} 条结果，系统保存排序最前的 3 条。
                </p>
              </div>
              <a
                v-if="detail.image_search?.search_page_url"
                :href="detail.image_search.search_page_url"
                target="_blank"
                rel="noopener noreferrer"
                class="button-link"
              >打开 1688 官方图搜页</a>
            </div>
            <div
              v-if="detail.image_search?.top1"
              class="image-search-primary-card"
            >
              <img
                v-if="detail.image_search.top1.image_url"
                :src="detail.image_search.top1.image_url"
                class="image-search-primary-image"
              />
              <div>
                <span class="cluster-badge">Top 1</span>
                <strong>{{ detail.image_search.top1.title || "1688 图搜商品" }}</strong>
                <p>
                  价格：{{ detail.image_search.top1.price || "-" }} · 销量：{{ detail.image_search.top1.sale_quantity || "-" }}
                </p>
                <a
                  v-if="detail.image_search.top1.product_url"
                  :href="detail.image_search.top1.product_url"
                  target="_blank"
                  rel="noopener noreferrer"
                >打开 1688 商品</a>
              </div>
            </div>
            <div
              v-if="showMoreImageSearch && detail.image_search?.products?.length > 1"
              class="image-search-more-grid"
            >
              <article
                v-for="item in detail.image_search.products.slice(1)"
                :key="item.rank"
                class="image-search-result-card"
              >
                <img v-if="item.image_url" :src="item.image_url" />
                <span>Top {{ item.rank }}</span>
                <strong>{{ item.title || "1688 图搜商品" }}</strong>
                <small>价格：{{ item.price || "-" }} · 销量：{{ item.sale_quantity || "-" }}</small>
                <a v-if="item.product_url" :href="item.product_url" target="_blank" rel="noopener noreferrer">打开商品</a>
              </article>
            </div>
            <button
              v-if="detail.image_search?.products?.length > 1"
              class="secondary"
              @click="showMoreImageSearch = !showMoreImageSearch"
            >
              {{ showMoreImageSearch ? "收起结果" : `查看更多（${detail.image_search.products.length - 1}）` }}
            </button>
            <p v-else-if="['pending', 'processing'].includes(detail.image_search?.status)" class="loading">正在执行 1688 以图找图</p>
            <p v-else-if="detail.image_search?.status === 'failed'" class="error">
              图搜失败：{{ detail.image_search.error_code || "UNKNOWN" }} · {{ detail.image_search.error_message || "未知错误" }}
            </p>
            <p v-else-if="!detail.image_search?.top1" class="empty">暂无以图找图结果</p>
          </section>
          <h3>平台同款</h3>
          <div
            v-if="detail.platform_same_products?.length"
            class="table-wrap detail-history-wrap"
          >
            <table>
              <thead>
                <tr>
                  <th>主图</th>
                  <th>Temu 商品</th>
                  <th>售价</th>
                  <th>日销量</th>
                  <th>总销量</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in detail.platform_same_products"
                  :key="item.id"
                >
                  <td>
                    <img
                      v-if="item.image_url"
                      :src="item.image_url"
                      class="product-image"
                    />
                  </td>
                  <td>
                    <a :href="item.product_url" target="_blank">{{
                      item.temu_product_id
                    }}</a>
                  </td>
                  <td>${{ number(item.sale_price) }}</td>
                  <td>{{ number(item.daily_sales, 0) }}</td>
                  <td>{{ number(item.total_sales, 0) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty">暂无已确认的 Temu 平台同款</p>
          <h3>显示货源 · 1688</h3>
          <div
            v-if="detail.source_products?.length"
            class="table-wrap detail-history-wrap"
          >
            <table>
              <thead>
                <tr>
                  <th>主图</th>
                  <th>1688 商品</th>
                  <th>每日笔数</th>
                  <th>总件数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in detail.source_products" :key="item.id">
                  <td>
                    <img
                      v-if="item.image_url"
                      :src="item.image_url"
                      class="product-image"
                    />
                  </td>
                  <td>
                    <a :href="item.product_url" target="_blank">{{
                      item.offer_id
                    }}</a>
                  </td>
                  <td>{{ number(item.daily_orders, 4) }}</td>
                  <td>{{ number(item.total_pieces, 0) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty">暂无已确认的 1688 货源</p>
          <h3>个人采集历史</h3>
          <div class="table-wrap detail-history-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>日销量</th>
                  <th>周销量</th>
                  <th>月销量</th>
                  <th>总销量</th>
                  <th>售价</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="snapshot in detail.snapshots" :key="snapshot.id">
                  <td>
                    {{
                      new Date(snapshot.collected_at).toLocaleString("zh-CN")
                    }}
                  </td>
                  <td>{{ number(snapshot.daily_sales, 0) }}</td>
                  <td>{{ number(snapshot.weekly_sales, 0) }}</td>
                  <td>{{ number(snapshot.monthly_sales, 0) }}</td>
                  <td>{{ number(snapshot.total_sales, 0) }}</td>
                  <td>${{ number(snapshot.sale_price) }}</td>
                </tr>
                <tr v-if="!detail.snapshots.length">
                  <td colspan="6" class="empty">暂无个人采集历史</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>
      </div>
      <p v-if="detailError" class="error">{{ detailError }}</p>
    </section>
  </div>
</template>
