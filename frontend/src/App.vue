<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "./api";
import AdminConsole from "./components/AdminConsole.vue";
import PlatformHome from "./components/PlatformHome.vue";
import Product1688Manager from "./components/Product1688Manager.vue";
import SimilarityAdmin from "./components/SimilarityAdmin.vue";
import TemuImageDownloadAdmin from "./components/TemuImageDownloadAdmin.vue";
import TemuManager from "./components/TemuManager.vue";
import UserManagement from "./components/UserManagement.vue";

const currentUser = ref(null);
const users = ref([]);
const selectedModule = ref(null);
const loginForm = reactive({ username: "admin", password: "" });
const loginError = ref("");
const isAdmin = computed(() => currentUser.value?.role === "admin");

function setPath(path) {
  if (window.location.pathname !== path) window.history.pushState({}, "", path);
}

function enterDefaultHome() {
  selectedModule.value = null;
  setPath(isAdmin.value ? "/console" : "/");
}

async function loadUsers() {
  if (!isAdmin.value) return;
  users.value = await api("/api/admin/users");
}

async function checkSession() {
  try {
    currentUser.value = await api("/api/auth/me");
    await loadUsers();
    if (isAdmin.value) {
      setPath("/console");
    } else if (window.location.pathname === "/console") {
      setPath("/");
    }
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
    await loadUsers();
    enterDefaultHome();
  } catch (error) {
    loginError.value = error.message;
  }
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => null);
  currentUser.value = null;
  users.value = [];
  selectedModule.value = null;
  setPath("/");
}

function selectModule(module) {
  selectedModule.value = module;
  if (isAdmin.value) setPath(`/console/${module}`);
}

function backHome() {
  selectedModule.value = null;
  setPath(isAdmin.value ? "/console" : "/");
}

window.addEventListener("session-expired", () => {
  currentUser.value = null;
  selectedModule.value = null;
  setPath("/");
});

window.addEventListener("popstate", () => {
  if (!currentUser.value) return;
  if (isAdmin.value && window.location.pathname.startsWith("/console/")) {
    selectedModule.value = window.location.pathname.split("/")[2] || null;
  } else {
    selectedModule.value = null;
  }
});

onMounted(async () => {
  await checkSession();
  if (isAdmin.value && window.location.pathname.startsWith("/console/")) {
    selectedModule.value = window.location.pathname.split("/")[2] || null;
  }
});
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

  <AdminConsole
    v-else-if="isAdmin && !selectedModule"
    :user="currentUser"
    @select="selectModule"
    @logout="logout"
  />

  <PlatformHome
    v-else-if="!isAdmin && !selectedModule"
    :user="currentUser"
    @select="selectModule"
    @logout="logout"
  />

  <Product1688Manager
    v-else-if="selectedModule === '1688'"
    :user="currentUser"
    :users="users"
    @home="backHome"
    @logout="logout"
  />

  <TemuManager
    v-else-if="selectedModule === 'temu'"
    :user="currentUser"
    :users="users"
    @home="backHome"
    @logout="logout"
  />

  <UserManagement
    v-else-if="isAdmin && selectedModule === 'users'"
    :users="users"
    @back="backHome"
    @reload-users="loadUsers"
  />

  <SimilarityAdmin
    v-else-if="isAdmin && selectedModule === 'similarity'"
    :users="users"
    @back="backHome"
  />

  <TemuImageDownloadAdmin
    v-else-if="isAdmin && selectedModule === 'temu-images'"
    :users="users"
    @back="backHome"
  />

  <AdminConsole
    v-else-if="isAdmin"
    :user="currentUser"
    @select="selectModule"
    @logout="logout"
  />

  <PlatformHome
    v-else
    :user="currentUser"
    @select="selectModule"
    @logout="logout"
  />
</template>
