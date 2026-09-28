<template>
  <div class="app">
    <header class="header">
      <h1>DeepClean 🧹 深度扫描 - 文件清理工具</h1>
    </header>
    <main class="main">
      <el-tabs v-model="activeTab" class="main-tabs">
        <el-tab-pane label="文件扫描" name="files">
          <FileList />
        </el-tab-pane>
        <el-tab-pane label="应用卸载" name="uninstall">
          <AppUninstaller />
        </el-tab-pane>
      </el-tabs>
    </main>
  </div>
</template>

<script>
import { ref } from 'vue'
import FileList from './components/FileList.vue'
import AppUninstaller from './components/AppUninstaller.vue'

export default {
  name: 'App',
  components: {
    FileList,
    AppUninstaller
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
