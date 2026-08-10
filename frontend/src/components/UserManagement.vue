<script setup>
import { reactive, ref } from "vue";
import { api } from "../api";

const props = defineProps({ users: { type: Array, required: true } });
const emit = defineEmits(["back", "reload-users"]);
const createdToken = ref("");
const userForm = reactive({ username: "", password: "", role: "user" });

async function reload() {
  emit("reload-users");
}

async function createManagedUser() {
  createdToken.value = "";
  const created = await api("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(userForm),
  });
  createdToken.value = created.ingest_token;
  Object.assign(userForm, { username: "", password: "", role: "user" });
  await reload();
}

async function toggleUser(user) {
  await api(`/api/admin/users/${user.id}/status`, {
    method: "PUT",
    body: JSON.stringify({ is_active: !user.is_active }),
  });
  await reload();
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
  await reload();
}
</script>

<template>
  <section class="content user-management console-content">
    <header>
      <div>
        <h2>用户管理</h2>
        <span>创建、禁用用户并重置密码和个人采集令牌</span>
      </div>
      <button class="secondary" @click="emit('back')">返回控制台</button>
    </header>
    <form class="user-create" @submit.prevent="createManagedUser">
      <input v-model="userForm.username" placeholder="用户名" required />
      <input
        v-model="userForm.password"
        type="password"
        minlength="8"
        placeholder="初始密码"
        required
      />
      <select v-model="userForm.role">
        <option value="user">普通用户</option>
        <option value="admin">管理员</option>
      </select>
      <button>创建用户</button>
    </form>
    <div v-if="createdToken" class="token-box">
      <strong>新采集令牌（仅本次显示）</strong>
      <code>{{ createdToken }}</code>
      <button @click="navigator.clipboard.writeText(createdToken)">复制</button>
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
          <tr v-for="user in props.users" :key="user.id">
            <td>{{ user.username }}</td>
            <td>{{ user.role }}</td>
            <td>{{ user.is_active ? "启用" : "禁用" }}</td>
            <td>{{ user.ingest_token_prefix || "未生成" }}</td>
            <td>{{ new Date(user.created_at).toLocaleString("zh-CN") }}</td>
            <td class="row-actions">
              <button @click="toggleUser(user)">
                {{ user.is_active ? "禁用" : "启用" }}
              </button>
              <button @click="resetPassword(user)">重置密码</button>
              <button @click="resetToken(user)">重置令牌</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
