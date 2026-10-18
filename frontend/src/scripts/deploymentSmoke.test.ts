import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  buildSmokeContextOptions,
  buildSmokeSessionStorageFlags,
  buildSmokeUrl,
  resolveChromiumExecutablePath,
  resolvePlaywrightChromium,
  resolvePlaywrightModuleSpecifier,
  resolveSmokeBaseUrl,
  readStoredGuestToken,
  resolveSmokeStorageStatePath,
  resolveUsableStorageState,
  validateDeploymentSmokeResult,
} from '../../scripts/smoke-dev-deployment-helpers.mjs';

const smokeScript = readFileSync(join(process.cwd(), 'scripts/smoke-dev-deployment.mjs'), 'utf8');

describe('deployment smoke helpers', () => {
  it('优先使用命令行 base-url，其次使用 SMOKE_BASE_URL，最后回退 dev 本机地址', () => {
    expect(resolveSmokeBaseUrl(['node', 'smoke', '--base-url', 'https://example.com'], {})).toBe(
      'https://example.com',
    );
    expect(resolveSmokeBaseUrl(['node', 'smoke'], { SMOKE_BASE_URL: 'https://env.example.com' })).toBe(
      'https://env.example.com',
    );
    expect(resolveSmokeBaseUrl(['node', 'smoke'], {})).toBe('http://127.0.0.1:3004');
  });

  it('构造 /chat/new smoke 地址时会去掉尾部斜杠', () => {
    expect(buildSmokeUrl('https://fusion.example.com/')).toBe('https://fusion.example.com/chat/new');
  });

  it('提供禁用中央会话恢复的 sessionStorage 标记', () => {
    expect(buildSmokeSessionStorageFlags()).toEqual({
      fusion_sso_logged_out: '1',
    });
  });

  it('Playwright 模块默认按包名加载，也支持临时安装目录路径', () => {
    const modulePath = '/tmp/smoke/node_modules/playwright/index.js';

    expect(resolvePlaywrightModuleSpecifier({})).toBe('playwright');
    expect(resolvePlaywrightModuleSpecifier({ PLAYWRIGHT_MODULE_PATH: modulePath })).toBe(
      pathToFileURL(modulePath).href,
    );
  });

  it('兼容 Playwright ESM 命名导出和 CJS default 导出', () => {
    const namedChromium = {};
    const defaultChromium = {};

    expect(resolvePlaywrightChromium({ chromium: namedChromium })).toBe(namedChromium);
    expect(resolvePlaywrightChromium({ default: { chromium: defaultChromium } })).toBe(defaultChromium);
    expect(() => resolvePlaywrightChromium({})).toThrow('无法加载 Playwright chromium');
  });

  it('支持 smoke runner 指定系统 Chromium 路径', () => {
    expect(resolveChromiumExecutablePath({})).toBeUndefined();
    expect(
      resolveChromiumExecutablePath({
        PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH: '/usr/bin/chromium-browser',
      }),
    ).toBe('/usr/bin/chromium-browser');
    expect(resolveChromiumExecutablePath({ CHROMIUM_EXECUTABLE_PATH: '/custom/chromium' })).toBe(
      '/custom/chromium',
    );
  });

  it('校验新对话页输入区、模型能力说明、下拉标签和控制台错误', () => {
    expect(() =>
      validateDeploymentSmokeResult({
        currentUrl: 'https://fusion.example.com/chat/new',
        hasApplicationError: false,
        inputVisible: true,
        modelCapabilityTextVisible: true,
        capabilityLabelsVisible: true,
        sameOrigin: true,
        consoleErrors: [],
        pageErrors: [],
      }),
    ).not.toThrow();

    expect(() =>
      validateDeploymentSmokeResult({
        currentUrl: 'https://fusion.example.com/chat/new',
        hasApplicationError: false,
        inputVisible: true,
        modelCapabilityTextVisible: false,
        capabilityLabelsVisible: true,
        sameOrigin: true,
        consoleErrors: [],
        pageErrors: [],
      }),
    ).toThrow('模型能力说明不可见');

    expect(() =>
      validateDeploymentSmokeResult({
        currentUrl: 'https://fusion.seanfield.org/chat/new',
        hasApplicationError: false,
        inputVisible: true,
        modelCapabilityTextVisible: true,
        capabilityLabelsVisible: true,
        sameOrigin: false,
        consoleErrors: [],
        pageErrors: [],
      }),
    ).toThrow('页面跳转到非目标来源');
  });

  it('browser smoke 使用稳定测试标识定位模型选择器，不依赖原生 title tooltip', () => {
    expect(smokeScript).toContain('MODEL_SELECTOR_TRIGGER_SELECTOR');
    expect(smokeScript).toContain('MODEL_SELECTOR_PANEL_SELECTOR');
    expect(smokeScript).not.toContain('[title');
  });

  it('复用上次保存的浏览器存储，冒烟通过后再保存，不在每次部署都领取新游客', () => {
    expect(resolveSmokeStorageStatePath({ SMOKE_STORAGE_STATE_PATH: '/cache/state.json' })).toBe('/cache/state.json');
    expect(resolveSmokeStorageStatePath({})).toBeNull();
    expect(buildSmokeContextOptions('/cache/state.json', () => true)).toEqual({ storageState: '/cache/state.json' });
    expect(buildSmokeContextOptions('/cache/state.json', () => false)).toEqual({});
    expect(buildSmokeContextOptions(null, () => true)).toEqual({});

    const validateAt = smokeScript.indexOf('validateDeploymentSmokeResult(result);');
    expect(smokeScript.indexOf('context.storageState({ path: storageStatePath })')).toBeGreaterThan(validateAt);
  });

  it('保存的游客已被清理（令牌返回 401）时不再使用保存的存储，按新访客打开', async () => {
    const state = JSON.stringify({
      cookies: [],
      origins: [
        {
          origin: 'http://127.0.0.1:3004',
          localStorage: [{ name: 'guest_session', value: JSON.stringify({ token: 'guest-token', userId: 'u', expiresAt: 1 }) }],
        },
      ],
    });
    expect(readStoredGuestToken(JSON.parse(state))).toBe('guest-token');
    expect(readStoredGuestToken({ origins: [] })).toBeNull();

    const requests: Array<{ url: string; auth: string | undefined }> = [];
    const deps = (status: number | Error) => ({
      baseUrl: 'http://127.0.0.1:3004',
      fileExists: () => true,
      readFile: () => state,
      fetchImpl: async (url: URL, init: { headers: Record<string, string> }) => {
        requests.push({ url: String(url), auth: init.headers.Authorization });
        if (status instanceof Error) throw status;
        return { status };
      },
    });

    await expect(resolveUsableStorageState('/cache/state.json', deps(401))).resolves.toBeNull();
    expect(requests[0]).toEqual({ url: 'http://127.0.0.1:3004/api/auth/me/entitlements', auth: 'Bearer guest-token' });
    await expect(resolveUsableStorageState('/cache/state.json', deps(200))).resolves.toBe('/cache/state.json');
    // 网络错误不等于游客失效，照常使用，由冒烟本身报告
    await expect(resolveUsableStorageState('/cache/state.json', deps(new Error('down')))).resolves.toBe('/cache/state.json');
    await expect(
      resolveUsableStorageState('/cache/state.json', { ...deps(200), fileExists: () => false }),
    ).resolves.toBeNull();
    await expect(resolveUsableStorageState(null, deps(200))).resolves.toBeNull();
  });
});
