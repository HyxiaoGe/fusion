const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

/**
 * Bundle分析脚本
 * 用于分析Next.js应用的打包文件大小和优化机会
 */

console.log('🔍 开始分析Bundle...\n');

// 运行生产构建
console.log('📦 执行生产构建...');
try {
  execSync('npm run build', { stdio: 'inherit' });
} catch (error) {
  console.error('❌ 构建失败:', error.message);
  process.exit(1);
}

// 分析.next目录的内容
const nextDir = path.join(process.cwd(), '.next');
const staticDir = path.join(nextDir, 'static');

// 获取文件大小
function getFileSize(filePath) {
  try {
    const stats = fs.statSync(filePath);
    return stats.size;
  } catch {
    return 0;
  }
}

// 格式化文件大小
function formatSize(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
}

// 递归获取目录下所有文件
function getAllFiles(dirPath, arrayOfFiles = []) {
  if (!fs.existsSync(dirPath)) return arrayOfFiles;
  
  const files = fs.readdirSync(dirPath);
  
  files.forEach(file => {
    const fullPath = path.join(dirPath, file);
    if (fs.statSync(fullPath).isDirectory()) {
      getAllFiles(fullPath, arrayOfFiles);
    } else {
      arrayOfFiles.push(fullPath);
    }
  });
  
  return arrayOfFiles;
}

// 分析JavaScript bundles
console.log('\n📊 JavaScript Bundle 分析:');
console.log('=' .repeat(50));

const jsFiles = getAllFiles(staticDir)
  .filter(file => file.endsWith('.js'))
  .map(file => ({
    path: file,
    size: getFileSize(file),
    relativePath: path.relative(staticDir, file)
  }))
  .sort((a, b) => b.size - a.size);

let totalJSSize = 0;
jsFiles.forEach(file => {
  totalJSSize += file.size;
  console.log(`📄 ${file.relativePath.padEnd(40)} ${formatSize(file.size)}`);
});

console.log(`\n💾 总JS大小: ${formatSize(totalJSSize)}`);

// 分析CSS bundles
console.log('\n🎨 CSS Bundle 分析:');
console.log('=' .repeat(50));

const cssFiles = getAllFiles(staticDir)
  .filter(file => file.endsWith('.css'))
  .map(file => ({
    path: file,
    size: getFileSize(file),
    relativePath: path.relative(staticDir, file)
  }))
  .sort((a, b) => b.size - a.size);

let totalCSSSize = 0;
cssFiles.forEach(file => {
  totalCSSSize += file.size;
  console.log(`🎨 ${file.relativePath.padEnd(40)} ${formatSize(file.size)}`);
});

console.log(`\n💄 总CSS大小: ${formatSize(totalCSSSize)}`);

// 分析图片和其他静态资源
console.log('\n🖼️ 静态资源分析:');
console.log('=' .repeat(50));

const otherFiles = getAllFiles(staticDir)
  .filter(file => !file.endsWith('.js') && !file.endsWith('.css'))
  .map(file => ({
    path: file,
    size: getFileSize(file),
    relativePath: path.relative(staticDir, file)
  }))
  .sort((a, b) => b.size - a.size);

let totalOtherSize = 0;
otherFiles.slice(0, 10).forEach(file => { // 只显示前10个最大的文件
  totalOtherSize += file.size;
  console.log(`📦 ${file.relativePath.padEnd(40)} ${formatSize(file.size)}`);
});

console.log(`\n📦 其他资源大小: ${formatSize(totalOtherSize)}`);

// 生成总结报告
const totalSize = totalJSSize + totalCSSSize + totalOtherSize;

console.log('\n📋 Bundle 总结报告:');
console.log('=' .repeat(50));
console.log(`🎯 总大小: ${formatSize(totalSize)}`);
console.log(`📄 JavaScript: ${formatSize(totalJSSize)} (${((totalJSSize / totalSize) * 100).toFixed(1)}%)`);
console.log(`🎨 CSS: ${formatSize(totalCSSSize)} (${((totalCSSSize / totalSize) * 100).toFixed(1)}%)`);
console.log(`📦 其他: ${formatSize(totalOtherSize)} (${((totalOtherSize / totalSize) * 100).toFixed(1)}%)`);

// 优化建议
console.log('\n💡 优化建议:');
console.log('=' .repeat(50));

if (totalJSSize > 1024 * 1024) { // 1MB
  console.log('⚠️  JavaScript bundle较大，考虑:');
  console.log('   • 使用动态导入进行代码分割');
  console.log('   • 启用tree shaking移除未使用代码');
  console.log('   • 检查是否引入了不必要的第三方库');
}

if (totalCSSSize > 500 * 1024) { // 500KB
  console.log('⚠️  CSS bundle较大，考虑:');
  console.log('   • 使用PurgeCSS移除未使用的样式');
  console.log('   • 考虑CSS-in-JS方案');
}

const largeFiles = [...jsFiles, ...cssFiles, ...otherFiles].filter(file => file.size > 100 * 1024);
if (largeFiles.length > 0) {
  console.log('📋 大文件列表 (>100KB):');
  largeFiles.slice(0, 5).forEach(file => {
    console.log(`   • ${file.relativePath}: ${formatSize(file.size)}`);
  });
}

console.log('\n✅ Bundle分析完成!');
console.log('\n📖 建议使用以下命令进一步分析:');
console.log('   • npm install --save-dev @next/bundle-analyzer');
console.log('   • 在next.config.js中启用bundle analyzer');
console.log('   • 使用webpack-bundle-analyzer进行可视化分析');

// 检查是否安装了bundle analyzer
const packageJsonPath = path.join(process.cwd(), 'package.json');
if (fs.existsSync(packageJsonPath)) {
  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
  const deps = { ...packageJson.dependencies, ...packageJson.devDependencies };
  
  if (!deps['@next/bundle-analyzer']) {
    console.log('\n🚀 快速安装bundle analyzer:');
    console.log('   npm install --save-dev @next/bundle-analyzer');
  }
}
