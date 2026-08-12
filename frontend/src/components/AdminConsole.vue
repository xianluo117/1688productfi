<script setup>
const emit = defineEmits(["select", "logout"]);
defineProps({ user: { type: Object, required: true } });

const entries = [
  {
    id: "1688",
    icon: "1688",
    title: "1688 产品管理",
    description: "管理商品分组、销量指标与采集历史",
  },
  {
    id: "temu",
    icon: "TEMU",
    title: "Temu 产品管理",
    description: "管理销量、价格、上架时间与店铺信息",
  },
  {
    id: "users",
    icon: "用户",
    title: "用户管理",
    description: "创建、禁用账号并管理采集令牌",
  },
  {
    id: "similarity",
    icon: "AI",
    title: "相似度检索",
    description: "管理向量模型、处理范围、任务与审核",
  },
  {
    id: "temu-images",
    icon: "图片",
    title: "Temu 图片下载",
    description: "按用户和分组补齐图片并查看实时下载日志",
  },
  {
    id: "temu-image-search",
    icon: "搜图",
    title: "1688 以图找图",
    description: "配置图搜 API、补跑 Temu 商品并查看任务日志",
  },
];
</script>

<template>
  <main class="platform-home console-home">
    <section class="platform-shell">
      <header class="platform-header">
        <div class="platform-brand">
          <span class="brand-mark">C</span>
          <div>
            <p class="eyebrow">ADMIN CONSOLE</p>
            <strong>管理员控制台</strong>
          </div>
        </div>
        <div class="platform-account">
          <div class="account-copy">
            <span>{{ user.username }}</span>
            <small>管理员</small>
          </div>
          <span class="account-avatar">{{
            user.username.slice(0, 1).toUpperCase()
          }}</span>
          <button class="secondary logout-button" @click="emit('logout')">
            退出登录
          </button>
        </div>
      </header>

      <section class="platform-intro">
        <p class="section-label">控制台</p>
        <h1>选择管理模块</h1>
        <p>产品、用户、图片下载和相似度检索已拆分为独立模块。</p>
      </section>

      <section class="platform-grid console-grid" aria-label="管理员模块列表">
        <button
          v-for="entry in entries"
          :key="entry.id"
          class="platform-card"
          @click="emit('select', entry.id)"
        >
          <span class="platform-icon">{{ entry.icon }}</span>
          <span class="platform-card-copy">
            <strong>{{ entry.title }}</strong>
            <small>{{ entry.description }}</small>
          </span>
          <span class="platform-enter">进入模块 <b>→</b></span>
        </button>
      </section>
    </section>
  </main>
</template>
