import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import zh from '@/lib/i18n/locales/zh-CN.json';
import en from '@/lib/i18n/locales/en-US.json';

const mocks = vi.hoisted(() => ({
  prompt: '',
  dispatch: vi.fn(),
  update: vi.fn((prompt: string) => ({ type: 'auth/updateUserSystemPrompt', payload: prompt })),
}));

vi.mock('@/redux/hooks', () => ({
  useAppDispatch: () => mocks.dispatch,
  useAppSelector: (selector: (state: unknown) => unknown) => selector({ auth: { user: { system_prompt: mocks.prompt } } }),
}));

vi.mock('@/redux/slices/authSlice', () => ({ updateUserSystemPrompt: mocks.update }));

let language: 'zh' | 'en' = 'zh';

function translate(key: string, values?: Record<string, unknown>) {
  const value = key.split('.').reduce<unknown>((current, part) => (
    current && typeof current === 'object' ? (current as Record<string, unknown>)[part] : undefined
  ), language === 'zh' ? zh : en);
  if (typeof value !== 'string') throw new Error(`缺少测试所需翻译: ${key}`);
  return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ''));
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: translate }),
}));

import SystemPrompt from './SystemPrompt';

const text = (key: string) => translate(`settings.personalization.${key}`);
const field = () => screen.getByRole('textbox', { name: text('label') });
const template = (key: string) => screen.getByRole('button', { name: text(`template.${key}.label`) });
const save = () => screen.getByRole('button', { name: text('save') });
const reset = () => screen.getByRole('button', { name: text('reset') });

function deferred() {
  let resolve!: (value: string) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<string>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

describe('SystemPrompt 个性化编辑', () => {
  beforeEach(() => {
    language = 'zh';
    mocks.prompt = '已保存的回答偏好';
    mocks.dispatch.mockReset();
    mocks.update.mockClear();
    mocks.dispatch.mockReturnValue({ unwrap: () => Promise.resolve(mocks.prompt) });
  });

  it('输入框提供标签、说明与计数，初始未修改时不可保存或还原', () => {
    render(<SystemPrompt />);

    expect(field()).toHaveValue(mocks.prompt);
    expect(field()).toHaveAttribute('aria-invalid', 'false');
    expect(field()).toHaveAccessibleDescription(`${text('hint')} ${mocks.prompt.length} / 1000`);
    expect(screen.getByText(text('unchanged'))).toBeInTheDocument();
    expect(save()).toBeDisabled();
    expect(reset()).toBeDisabled();
    expect(template('engineer')).toHaveAttribute('aria-pressed', 'false');
    expect(template('engineer')).toHaveAccessibleDescription(text('template.engineer.description'));
  });

  it('模板按草稿内容选中，编辑后取消选中，还原恢复已保存内容', () => {
    mocks.prompt = text('template.learner.content');
    render(<SystemPrompt />);
    expect(template('learner')).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(template('engineer'));
    expect(field()).toHaveValue(text('template.engineer.content'));
    expect(template('engineer')).toHaveAttribute('aria-pressed', 'true');
    expect(template('learner')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(text('unsaved'))).toBeInTheDocument();
    expect(save()).toBeEnabled();

    fireEvent.change(field(), { target: { value: `${text('template.engineer.content')} 补充偏好` } });
    expect(template('engineer')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(reset());
    expect(field()).toHaveValue(mocks.prompt);
    expect(template('learner')).toHaveAttribute('aria-pressed', 'true');
    expect(save()).toBeDisabled();
  });

  it('1000 字允许保存，超过上限时提供错误说明并阻止提交', () => {
    render(<SystemPrompt />);
    fireEvent.change(field(), { target: { value: '字'.repeat(1000) } });
    expect(save()).toBeEnabled();
    expect(field()).toHaveAttribute('aria-invalid', 'false');

    fireEvent.change(field(), { target: { value: '字'.repeat(1001) } });
    const message = translate('settings.personalization.overLimit', { limit: 1000 });
    expect(field()).toHaveAttribute('aria-invalid', 'true');
    expect(field()).toHaveAccessibleDescription(`${text('hint')} 1001 / 1000 ${message}`);
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(save()).toBeDisabled();
    fireEvent.click(save());
    expect(mocks.dispatch).not.toHaveBeenCalled();
  });

  it('保存中锁定编辑和模板，连续点击只发送一次，并在 Redux 更新后显示已保存', async () => {
    const pending = deferred();
    mocks.dispatch.mockReturnValue({ unwrap: () => pending.promise });
    const view = render(<SystemPrompt />);
    fireEvent.change(field(), { target: { value: '新的回答偏好' } });
    const saveButton = save();

    act(() => {
      saveButton.click();
      saveButton.click();
    });

    expect(mocks.update).toHaveBeenCalledExactlyOnceWith('新的回答偏好');
    expect(mocks.dispatch).toHaveBeenCalledTimes(1);
    expect(field()).toBeDisabled();
    expect(template('engineer')).toBeDisabled();
    expect(reset()).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent(text('saving'));
    fireEvent.change(field(), { target: { value: '保存期间不应写入的新草稿' } });
    fireEvent.click(template('engineer'));
    expect(field()).toHaveValue('新的回答偏好');

    await act(async () => {
      mocks.prompt = '新的回答偏好';
      pending.resolve(mocks.prompt);
      await pending.promise;
    });
    view.rerender(<SystemPrompt />);

    expect(screen.getByRole('status')).toHaveTextContent(text('saved'));
    expect(field()).toBeEnabled();
    expect(save()).toBeDisabled();
    expect(screen.getByText(text('unchanged'))).toBeInTheDocument();
  });

  it('保存失败保留草稿并提供本地化错误，用户编辑后清除旧错误', async () => {
    mocks.dispatch.mockReturnValue({ unwrap: () => Promise.reject('backend_protocol_error') });
    render(<SystemPrompt />);
    fireEvent.change(field(), { target: { value: '等待重试的草稿' } });
    fireEvent.click(save());

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(text('saveFailed')));
    expect(screen.queryByText('backend_protocol_error')).toBeNull();
    expect(field()).toHaveValue('等待重试的草稿');
    expect(field()).toBeEnabled();
    expect(save()).toBeEnabled();

    fireEvent.change(field(), { target: { value: '调整后的草稿' } });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText(text('unsaved'))).toBeInTheDocument();
  });

  it('英文界面套用英文模板，按钮状态仍与草稿一致', () => {
    language = 'en';
    render(<SystemPrompt />);
    fireEvent.click(template('writing'));

    expect(field()).toHaveValue(text('template.writing.content'));
    expect(template('writing')).toHaveAttribute('aria-pressed', 'true');
    expect(field()).toHaveAccessibleName(text('label'));
    expect(save()).toBeEnabled();
  });
});
