<template>
  <div class="app">
    <header class="header">
      <h1>DeepClean 🧹 深度扫描 - 文件清理工具</h1>
    </header>
    <main class="main">
      <!-- 全局 Dashboard: 系统状态 + 清理成果统计 + 删除历史(不再属于某个 Tab) -->
      <Dashboard />
      <el-tabs v-model="activeTab" class="main-tabs">
        <el-tab-pane label="文件扫描" name="files">
          <FileList />
        </el-tab-pane>
        <el-tab-pane label="应用卸载" name="uninstall">
          <AppUninstaller />
        </el-tab-pane>
        <el-tab-pane label="残留清理" name="orphans" lazy>
          <OrphanSweeper />
        </el-tab-pane>
      </el-tabs>
    </main>
  </div>
</template>

<script>
import { ref } from 'vue'
import Dashboard from './components/Dashboard.vue'
import FileList from './components/FileList.vue'
import AppUninstaller from './components/AppUninstaller.vue'
import OrphanSweeper from './components/OrphanSweeper.vue'

export default {
  name: 'App',
  components: {
    Dashboard,
    FileList,
    AppUninstaller,
    OrphanSweeper
  },
  setup() {
    const activeTab = ref('files')
    return { activeTab }
  }
}
</script>

<style>
body {
  margin: 0;
  background-color: #f5f7fa;
}

.app {
  min-height: 100vh;
}

.header {
  background-color: #409eff;
  color: white;
  padding: 1rem;
  text-align: center;
}

.header h1 {
  margin: 0;
  font-size: 1.5rem;
}

.main {
  max-width: 1600px;
  margin: 0 auto;
  padding: 1rem;
}

.main-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}
</style>
