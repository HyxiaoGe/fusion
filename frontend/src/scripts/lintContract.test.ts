import { spawnSync } from 'node:child_process';
import { join } from 'node:path';

import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

const frontendRoot = process.cwd();

describe('前端 lint 门禁', () => {
  it('忽略构建产物目录', async () => {
    const eslint = new ESLint({ cwd: frontendRoot });

    await expect(
      eslint.isPathIgnored(join(frontendRoot, '.next', 'lint-contract.js')),
    ).resolves.toBe(true);
  });

  it('通过 npm 脚本公开 ESLint 且不依赖旧 eslintignore', () => {
    const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
    const result = spawnSync(npmCommand, ['run', 'lint', '--', '--version'], {
      cwd: frontendRoot,
      encoding: 'utf8',
    });
    const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`;

    expect(output).not.toContain('ESLintIgnoreWarning');
    expect(result.status, output).toBe(0);
    expect(output).toMatch(/v9\.\d+\.\d+/);
  });
});
