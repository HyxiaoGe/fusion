import { pathToFileURL } from 'node:url';

const DEFAULT_BASE_URL = 'http://127.0.0.1:3004';

export const MODEL_SELECTOR_TRIGGER_SELECTOR = '[data-testid="model-selector-trigger"]';
export const MODEL_SELECTOR_PANEL_SELECTOR = '[data-testid="model-selector-panel"]';
export const MODEL_CAPABILITY_LABEL_TEXTS = ['可联网', '不可联网', '读图', '工具', '长上下文', '深度任务'];

export class DeploymentSmokeError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = 'DeploymentSmokeError';
    this.details = details;
  }
}

function normalizeBaseUrl(value) {
  return String(value || DEFAULT_BASE_URL).replace(/\/+$/u, '');
}

export function resolveSmokeBaseUrl(argv = process.argv, env = process.env) {
  const equalsArg = argv.find((arg) => arg.startsWith('--base-url='));
  if (equalsArg) {
    return normalizeBaseUrl(equalsArg.slice('--base-url='.length));
  }

  const index = argv.indexOf('--base-url');
  if (index >= 0 && argv[index + 1]) {
    return normalizeBaseUrl(argv[index + 1]);
  }

  return normalizeBaseUrl(env.SMOKE_BASE_URL);
}

export function buildSmokeUrl(baseUrl) {
  return `${normalizeBaseUrl(baseUrl)}/chat/new`;
}

export function buildSmokeSessionStorageFlags() {
  return {
    fusion_sso_logged_out: '1',
  };
}

export function isSameOrigin(currentUrl, targetUrl) {
  try {
    return new URL(currentUrl).origin === new URL(targetUrl).origin;
  } catch {
    return false;
  }
}

export function resolvePlaywrightModuleSpecifier(env = process.env) {
  if (env.PLAYWRIGHT_MODULE_PATH) {
    return pathToFileURL(env.PLAYWRIGHT_MODULE_PATH).href;
  }

  return 'playwright';
}

export function resolvePlaywrightChromium(playwrightModule) {
  const chromium = playwrightModule?.chromium || playwrightModule?.default?.chromium;
  if (!chromium) {
    throw new DeploymentSmokeError('无法加载 Playwright chromium', {
      exports: Object.keys(playwrightModule || {}),
    });
  }

  return chromium;
}

export function resolveChromiumExecutablePath(env = process.env) {
  return env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || env.CHROMIUM_EXECUTABLE_PATH || undefined;
}

export function validateDeploymentSmokeResult(result) {
  const failures = [];

  if (result.hasApplicationError) failures.push('页面出现 Application error');
  if (!result.inputVisible) failures.push('新对话输入区不可见');
  if (!result.modelCapabilityTextVisible) failures.push('模型能力说明不可见');
  if (!result.capabilityLabelsVisible) failures.push('模型下拉能力标签不可见');
  if (result.sameOrigin === false) failures.push('页面跳转到非目标来源');
  if (result.consoleErrors?.length) failures.push(`控制台错误 ${result.consoleErrors.length} 条`);
  if (result.pageErrors?.length) failures.push(`页面异常 ${result.pageErrors.length} 条`);

  if (failures.length > 0) {
    throw new DeploymentSmokeError(failures.join('；'), result);
  }
}

/**
 * 未登录打开首页会自动领取游客身份，而冒烟每次都从 dev 内网同一地址发起，
 * 每次部署都领新游客会撞上「同一 IP 每天新建游客」上限（429）。
 * 有上次保存的浏览器存储（含游客令牌）就接着用；游客被清理或令牌失效时
 * 丢弃保存的存储重新领取（见 resolveUsableStorageState），一天最多新增一个。
 */
export function resolveSmokeStorageStatePath(env = process.env) {
  return env.SMOKE_STORAGE_STATE_PATH || null;
}

/** 从保存的浏览器存储里读出游客令牌；没有或格式不对时返回 null。 */
export function readStoredGuestToken(storageState) {
  for (const origin of storageState?.origins ?? []) {
    const item = (origin.localStorage ?? []).find((entry) => entry.name === 'guest_session');
    if (!item) continue;
    try {
      const token = JSON.parse(item.value)?.token;
      return typeof token === 'string' && token ? token : null;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * 冒烟的游客从不发消息，24 小时后会被游客清理删掉。带着已删除游客的令牌打开页面，
 * 前端会先收到一串 401 再重新领取，这些 401 会被记成控制台错误让冒烟失败。
 * 所以打开页面前先用令牌请求一次：明确返回 401 就不用保存的存储，按新访客打开；
 * 网络错误等其他情况照常使用，交给冒烟本身报告。
 */
export async function resolveUsableStorageState(storageStatePath, { baseUrl, fileExists, readFile, fetchImpl }) {
  if (!storageStatePath || !fileExists(storageStatePath)) return null;
  let token;
  try {
    token = readStoredGuestToken(JSON.parse(readFile(storageStatePath, 'utf8')));
  } catch {
    return null;
  }
  if (!token) return storageStatePath;
  try {
    const response = await fetchImpl(new URL('/api/auth/me/entitlements', baseUrl), {
      headers: { Authorization: `Bearer ${token}` },
    });
    return response.status === 401 ? null : storageStatePath;
  } catch {
    return storageStatePath;
  }
}

export function buildSmokeContextOptions(storageStatePath, fileExists) {
  return storageStatePath && fileExists(storageStatePath) ? { storageState: storageStatePath } : {};
}
