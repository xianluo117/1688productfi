<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'

const admin = ref(null)
const loginForm = reactive({ username: 'admin', password: '' })
const loginError = ref('')
const groups = ref([])
const selectedGroupId = ref(null)
const products = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(false)
const detail = ref(null)
const newGroupName = ref('')
const sort = reactive({ field: 'daily_orders', direction: 'desc' })

const selectedGroup = computed(() => groups.value.find((group) => group.id === selectedGroupId.value))
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (response.status === 401) {
    admin.value = null
    throw new Error('登录已失效')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `请求失败：${response.status}`)
  }
  if (response.status === 204) return null
  return response.json()
}

async function checkSession() {
  try {
    admin.value = await api('/api/auth/me')
    await loadGroups()
  } catch {
    admin.value = null
  }
}

async function login() {
  loginError.value = ''
  try {
    admin.value = await api('/api/auth/login', { method: 'POST', body: JSON.stringify(loginForm) })
    loginForm.password = ''
    await loadGroups()
  } catch (error) {
    loginError.value = error.message
  }
}

async function logout() {
  await api('/api/auth/logout', { method: 'POST' }).catch(() => null)
  admin.value = null
}

async function loadGroups() {
  groups.value = await api('/api/groups')
  if (!groups.value.length) {
    selectedGroupId.value = null
    products.value = []
    total.value = 0
    return
  }
  if (!groups.value.some((group) => group.id === selectedGroupId.value)) {
    selectGroup(groups.value[0])
  } else {
    await loadProducts()
  }
}

function selectGroup(group) {
  selectedGroupId.value = group.id
  sort.field = group.default_sort_field
  sort.direction = group.default_sort_direction
  page.value = 1
  detail.value = null
  loadProducts()
}

async function createGroup() {
  const name = newGroupName.value.trim()
  if (!name) return
  const group = await api('/api/groups', { method: 'POST', body: JSON.stringify({ name }) })
  newGroupName.value = ''
  await loadGroups()
  selectGroup(group)
}

async function renameGroup() {
  if (!selectedGroup.value) return
  const name = window.prompt('新的分组名称', selectedGroup.value.name)?.trim()
  if (!name || name === selectedGroup.value.name) return
  await api(`/api/groups/${selectedGroup.value.id}`, { method: 'PUT', body: JSON.stringify({ name }) })
  await loadGroups()
}

async function deleteGroup() {
  if (!selectedGroup.value || !window.confirm(`删除分组“${selectedGroup.value.name}”？商品和历史不会被删除。`)) return
  await api(`/api/groups/${selectedGroup.value.id}`, { method: 'DELETE' })
  await loadGroups()
}

async function loadProducts() {
  if (!selectedGroupId.value) return
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: String(page.value),
      page_size: String(pageSize),
      sort_field: sort.field,
      sort_direction: sort.direction,
    })
    const result = await api(`/api/groups/${selectedGroupId.value}/products?${params}`)
    products.value = result.items
    total.value = result.total
  } finally {
    loading.value = false
  }
}

async function toggleSort(field) {
  sort.direction = sort.field === field && sort.direction === 'desc' ? 'asc' : 'desc'
  sort.field = field
  page.value = 1
  await api(`/api/groups/${selectedGroupId.value}`, {
    method: 'PUT',
    body: JSON.stringify({ default_sort_field: sort.field, default_sort_direction: sort.direction }),
  })
  const group = selectedGroup.value
  if (group) {
    group.default_sort_field = sort.field
    group.default_sort_direction = sort.direction
  }
  await loadProducts()
}

function sortMark(field) {
  return sort.field === field ? (sort.direction === 'asc' ? ' ↑' : ' ↓') : ''
}

async function showDetail(product) {
  detail.value = await api(`/api/products/${product.id}`)
}

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || value === '') return '-'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

watch(page, loadProducts)
onMounted(checkSession)
</script>

<template>
  <main v-if="!admin" class="login-page">
    <form class="login-card" @submit.prevent="login">
      <h1>1688 商品管理</h1>
      <input v-model="loginForm.username" autocomplete="username" placeholder="管理员账号">
      <input v-model="loginForm.password" autocomplete="current-password" type="password" placeholder="管理员密码">
      <button>登录</button>
      <p v-if="loginError" class="error">{{ loginError }}</p>
    </form>
  </main>

  <div v-else class="app-shell">
    <aside>
      <h1>商品管理</h1>
      <div class="new-group">
        <input v-model="newGroupName" placeholder="新分组名称" @keyup.enter="createGroup">
        <button @click="createGroup">新增</button>
      </div>
      <nav>
        <button
          v-for="group in groups"
          :key="group.id"
          :class="{ active: group.id === selectedGroupId }"
          @click="selectGroup(group)"
        >{{ group.name }}</button>
      </nav>
      <button class="logout" @click="logout">退出登录</button>
    </aside>

    <section class="content">
      <header>
        <div>
          <h2>{{ selectedGroup?.name || '请先创建分组' }}</h2>
          <span v-if="selectedGroup">共 {{ total }} 个商品</span>
        </div>
        <div v-if="selectedGroup" class="group-actions">
          <button @click="renameGroup">重命名</button>
          <button class="danger" @click="deleteGroup">删除分组</button>
        </div>
      </header>

      <div v-if="selectedGroup" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>图片</th>
              <th @click="toggleSort('offer_id')">商品 ID{{ sortMark('offer_id') }}</th>
              <th @click="toggleSort('listed_days')">上架天数{{ sortMark('listed_days') }}</th>
              <th @click="toggleSort('daily_orders')">每日笔数{{ sortMark('daily_orders') }}</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="product in products" :key="product.id">
              <td>
                <img v-if="product.image_url" :src="product.image_url" loading="lazy" class="product-image">
                <div v-else class="image-placeholder">暂无图片</div>
              </td>
              <td><a :href="product.product_url" target="_blank" rel="noopener noreferrer">{{ product.offer_id }}</a></td>
              <td>{{ product.listed_days }}</td>
              <td>{{ formatNumber(product.daily_orders) }}</td>
              <td><button @click="showDetail(product)">详情</button></td>
            </tr>
            <tr v-if="!loading && !products.length"><td colspan="5" class="empty">此分组暂无商品</td></tr>
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
      <section class="drawer">
        <button class="close" @click="detail = null">×</button>
        <img v-if="detail.image_url" :src="detail.image_url" class="detail-image">
        <h2>商品 {{ detail.offer_id }}</h2>
        <dl>
          <dt>上架日期</dt><dd>{{ detail.listing_date }}</dd>
          <dt>上架天数</dt><dd>{{ detail.listed_days }}</dd>
          <dt>总笔数</dt><dd>{{ formatNumber(detail.total_orders, 0) }}</dd>
          <dt>总件数</dt><dd>{{ formatNumber(detail.total_pieces, 0) }}</dd>
          <dt>平均每笔件数</dt><dd>{{ formatNumber(detail.average_pieces_per_order) }}</dd>
          <dt>计算天数</dt><dd>{{ detail.calculation_days }}</dd>
          <dt>每日笔数</dt><dd>{{ formatNumber(detail.daily_orders) }}</dd>
          <dt>图片缓存</dt><dd>{{ detail.image_status }}</dd>
          <dt>所属分组</dt><dd>{{ detail.groups.map(group => group.name).join('、') }}</dd>
        </dl>
        <h3>采集历史</h3>
        <table class="history">
          <thead><tr><th>时间</th><th>天数</th><th>笔数</th><th>件数</th><th>每日笔数</th></tr></thead>
          <tbody>
            <tr v-for="snapshot in detail.snapshots" :key="snapshot.id">
              <td>{{ new Date(snapshot.collected_at).toLocaleString('zh-CN') }}</td>
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
