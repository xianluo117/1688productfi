<script setup>
const emit = defineEmits(["select", "logout"]);
defineProps({ user: { type: Object, required: true } });
</script>

<template>
  <main class="platform-home">
    <section class="platform-shell">
      <header class="platform-header">
        <div class="platform-brand">
          <span class="brand-mark">P</span>
          <div>
            <p class="eyebrow">PRODUCT DATA CENTER</p>
            <strong>产品数据管理中心</strong>
          </div>
        </div>
        <div class="platform-account">
          <div class="account-copy">
            <span>{{ user.username }}</span>
            <small>{{ user.role === "admin" ? "管理员" : "普通用户" }}</small>
          </div>
          <span class="account-avatar">{{ user.username.slice(0, 1).toUpperCase() }}</span>
          <button class="secondary logout-button" @click="emit('logout')">
            退出登录
          </button>
        </div>
      </header>

      <section class="platform-intro">
        <p class="section-label">工作台</p>
        <h1>选择产品平台</h1>
        <p>进入对应平台，管理商品数据、采集记录与业务指标。</p>
      </section>

      <section class="platform-grid" aria-label="产品平台列表">
        <button
          class="platform-card platform-1688"
          @click="emit('select', '1688')"
        >
          <span class="platform-icon">1688</span>
          <span class="platform-card-copy">
            <strong>1688 产品管理</strong>
            <small>管理商品分组、当前指标与个人采集历史</small>
            <span class="platform-features">
              <span>商品分组</span>
              <span>销量指标</span>
              <span>采集历史</span>
            </span>
          </span>
          <span class="platform-enter">进入平台 <b>→</b></span>
        </button>

        <button
          class="platform-card platform-temu"
          @click="emit('select', 'temu')"
        >
          <span class="platform-icon">TEMU</span>
          <span class="platform-card-copy">
            <strong>Temu 产品管理</strong>
            <small>查看销量、价格、上架时间与店铺经营信息</small>
            <span class="platform-features">
              <span>销量趋势</span>
              <span>价格数据</span>
              <span>店铺类型</span>
            </span>
          </span>
          <span class="platform-enter">进入平台 <b>→</b></span>
        </button>
      </section>

      <footer class="platform-footer">
        <span>数据按用户权限隔离</span>
        <span>·</span>
        <span>当前账号：{{ user.username }}</span>
      </footer>
    </section>
  </main>
</template>
