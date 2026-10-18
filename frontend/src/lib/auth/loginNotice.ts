import toast from 'react-hot-toast';
import i18n from '@/lib/i18n';

/** 交互式登录（授权回跳、邮箱验证码）在本标签完成后提示一次；静默恢复与跨标签同步不走这里。 */
export function notifyLoginSucceeded(email?: string | null): void {
  toast.success(
    email ? i18n.t('auth.loginSucceededWithEmail', { email }) : i18n.t('auth.loginSucceeded'),
  );
}
