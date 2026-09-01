<script setup lang="ts">
const pageTitle = 'CodingAgentNeo Web'
const connectionStatus = '尚未连接 Agent'
</script>

<template>
  <main class="placeholder-shell">
    <section
      class="placeholder-card"
      aria-labelledby="page-title"
    >
      <p class="eyebrow">
        CodingAgentNeo
      </p>
      <h1 id="page-title">
        {{ pageTitle }}
      </h1>
      <p
        class="status"
        role="status"
        aria-live="polite"
      >
        {{ connectionStatus }}
      </p>
      <p>
        前端基础工程已就绪。当前页面只用于确认本地 Web 项目可以启动，尚未连接 Agent 服务。
      </p>
    </section>
  </main>
</template>
