<script setup>
const emit = defineEmits(["select", "logout"]);
defineProps({ user: { type: Object, required: true } });
</script>

<template>
  <main class="platform-home">
    <section class="platform-header">
      <div>
        <p class="eyebrow">PRODUCT DATA CENTER</p>
        <h1>选择产品平台</h1>
        <p>
          {{ user.username }} · {{ user.role === "admin" ? "管理员" : "用户" }}
        </p>
      </div>
      <button class="secondary" @click="emit('logout')">退出登录</button>
    </section>
    <section class="platform-grid">
      <button
        class="platform-card platform-1688"
        @click="emit('select', '1688')"
      >
        <span class="platform-logo">1688</span>
        <strong>1688 产品管理</strong>
        <small>查看分组、当前指标与个人采集历史</small>
      </button>
      <button
        class="platform-card platform-temu"
        @click="emit('select', 'temu')"
      >
        <span class="platform-logo">TEMU</span>
        <strong>Temu 产品管理</strong>
        <small>查看销量、价格、上架时间与店铺类型</small>
      </button>
    </section>
  </main>
</template>
