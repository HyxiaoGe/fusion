"use client";

import { Github, KeyRound, Loader2, Mail } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { EmailCodeLoginPanel } from "@/components/auth/EmailCodeLoginPanel";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import {
  cancelEmailCodeLogin,
  resendEmailCodeLogin,
  startEmailCodeLogin,
  verifyEmailCodeLogin,
} from "@/lib/auth/emailCodeAuth";
import { isAuthConfigured } from "@/lib/auth/auth-sdk";
import {
  getEmailLoginCapabilities,
  startSsoLogin,
  type EmailLoginCapabilities,
  type SsoProvider,
} from "@/lib/auth/authService";
import "@/lib/i18n";
import { useAppDispatch } from "@/redux/hooks";
import { completeEmailCodeLogin } from "@/redux/slices/authSlice";
import glassStyles from "@/components/ui/GlassLens.module.css";
import styles from "./LoginDialog.module.css";

const EMAIL_LOGIN_UNAVAILABLE: EmailLoginCapabilities = { headless: false };

function resetLight(lens: HTMLElement | null) {
  if (!lens) return;
  lens.style.setProperty("--glint-x", "38%");
  lens.style.setProperty("--glint-y", "24%");
  lens.style.setProperty("--corner-tl", ".68");
  lens.style.setProperty("--corner-br", ".68");
}

function pointLight(lens: HTMLElement | null, rect: DOMRect, clientX: number, clientY: number) {
  if (!lens || !rect.width || !rect.height) return;
  const x = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  const y = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
  const diagonal = (x + y) / 2;
  lens.style.setProperty("--glint-x", `${x * rect.width}px`);
  lens.style.setProperty("--glint-y", `${y * rect.height}px`);
  lens.style.setProperty("--corner-tl", (0.48 + 0.42 * (1 - diagonal)).toFixed(2));
  lens.style.setProperty("--corner-br", (0.48 + 0.42 * diagonal).toFixed(2));
}

export function LoginDialog({
  open,
  onOpenChange,
  trigger,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: React.ReactNode;
}) {
  const dispatch = useAppDispatch();
  const [internalOpen, setInternalOpen] = useState(false);
  const [view, setView] = useState<"methods" | "email">("methods");
  const [isGitHubLoading, setIsGitHubLoading] = useState(false);
  const [isGoogleLoading, setIsGoogleLoading] = useState(false);
  const [emailCapabilities, setEmailCapabilities] = useState<EmailLoginCapabilities>(EMAIL_LOGIN_UNAVAILABLE);
  const [criticalOperation, setCriticalOperation] = useState(false);
  const criticalOperationRef = useRef(false);
  const dialogLensRef = useRef<HTMLDivElement>(null);
  const methodListRef = useRef<HTMLDivElement>(null);
  const methodLensRef = useRef<HTMLDivElement>(null);
  const { toast } = useToast();
  const { t } = useTranslation();
  const isControlled = open !== undefined;
  const requestedOpen = isControlled ? open : internalOpen;
  // 外层受控 prop 即使在 verify 途中变成 false，也要等 authorization code 完成/失败后再关闭。
  const dialogOpen = criticalOperation ? true : requestedOpen;
  const isAnyLoginLoading = isGitHubLoading || isGoogleLoading;

  const methodFromTarget = (target: EventTarget | null): HTMLButtonElement | null => {
    if (!(target instanceof Element)) return null;
    const button = target.closest<HTMLButtonElement>("button[data-login-method]");
    return button && methodListRef.current?.contains(button) ? button : null;
  };

  const moveMethodLens = (button: HTMLButtonElement | null) => {
    const lens = methodLensRef.current;
    const list = methodListRef.current;
    if (!lens || !list || !button) {
      lens?.classList.remove(glassStyles.visible);
      return;
    }
    const buttonRect = button.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    lens.style.setProperty("--lens-x", `${buttonRect.left - listRect.left}px`);
    lens.style.setProperty("--lens-y", `${buttonRect.top - listRect.top}px`);
    lens.style.width = `${buttonRect.width}px`;
    lens.style.height = `${buttonRect.height}px`;
    lens.classList.add(glassStyles.visible);
  };

  useEffect(() => {
    if (!dialogOpen) return;
    const release = () => methodLensRef.current?.classList.remove(glassStyles.pressed);
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
    };
  }, [dialogOpen]);

  const setCritical = (critical: boolean) => {
    criticalOperationRef.current = critical;
    setCriticalOperation(critical);
  };

  const handleDialogOpenChange = (nextOpen: boolean) => {
    if (!nextOpen && criticalOperationRef.current) return;
    if (!isControlled) setInternalOpen(nextOpen);
    onOpenChange?.(nextOpen);
  };

  useEffect(() => {
    let active = true;
    setEmailCapabilities(EMAIL_LOGIN_UNAVAILABLE);
    if (!dialogOpen) return () => { active = false; };

    void getEmailLoginCapabilities()
      .then((capabilities) => {
        if (active) setEmailCapabilities(capabilities);
      })
      .catch(() => {
        if (active) setEmailCapabilities(EMAIL_LOGIN_UNAVAILABLE);
      });

    return () => { active = false; };
  }, [dialogOpen]);

  useEffect(() => {
    if (dialogOpen) return;
    setView("methods");
    setIsGitHubLoading(false);
    setIsGoogleLoading(false);
    criticalOperationRef.current = false;
    setCriticalOperation(false);
  }, [dialogOpen]);

  const resetLoginLoading = () => {
    setIsGitHubLoading(false);
    setIsGoogleLoading(false);
  };

  const startOAuthLogin = (provider: SsoProvider) => {
    if (!isAuthConfigured()) {
      toast({ message: t("auth.configurationMissing"), type: "error" });
      resetLoginLoading();
      return;
    }

    void startSsoLogin(provider).catch(() => {
      toast({ message: t("auth.loginFailed"), type: "error" });
      resetLoginLoading();
    });
  };

  const handleGitHubLogin = () => {
    setIsGitHubLoading(true);
    startOAuthLogin("github");
  };

  const handleGoogleLogin = () => {
    setIsGoogleLoading(true);
    startOAuthLogin("google");
  };

  const handleEmailLogin = () => {
    if (!isAuthConfigured()) {
      toast({ message: t("auth.configurationMissing"), type: "error" });
      return;
    }
    setView("email");
  };

  const handleVerifyEmailCode = async (input: Parameters<typeof verifyEmailCodeLogin>[0]) => {
    await verifyEmailCodeLogin(input);
    await dispatch(completeEmailCodeLogin()).unwrap();
  };

  const handleEmailAuthenticated = () => handleDialogOpenChange(false);

  return (
    <Dialog open={dialogOpen} onOpenChange={handleDialogOpenChange}>
      {trigger && <DialogTrigger asChild>{trigger}</DialogTrigger>}
      <DialogContent
        className={`sm:max-w-md ${styles.surface}`}
        overlayClassName={styles.overlay}
        closeLabel={t("auth.closeDialog")}
        showCloseButton={!criticalOperation}
        onEscapeKeyDown={(event) => {
          if (criticalOperationRef.current) event.preventDefault();
        }}
        onPointerDownOutside={(event) => {
          if (criticalOperationRef.current) event.preventDefault();
        }}
        onPointerMove={(event) => {
          if (event.pointerType === "touch") return;
          pointLight(dialogLensRef.current, event.currentTarget.getBoundingClientRect(), event.clientX, event.clientY);
        }}
        onPointerLeave={(event) => {
          if (event.pointerType === "touch") return;
          resetLight(dialogLensRef.current);
        }}
      >
        <div className={`${glassStyles.lens} ${glassStyles.visible} ${styles.dialogLens}`} ref={dialogLensRef} aria-hidden="true" />
        <div className={styles.content}>
          {view === "email" ? (
            <div className={styles.emailPanel}>
              <EmailCodeLoginPanel
                active={dialogOpen}
                start={startEmailCodeLogin}
                resend={resendEmailCodeLogin}
                verify={handleVerifyEmailCode}
                cancel={cancelEmailCodeLogin}
                onBackToMethods={() => setView("methods")}
                onAuthenticated={handleEmailAuthenticated}
                onCriticalOperationChange={setCritical}
              />
            </div>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>{t("auth.loginTitle")}</DialogTitle>
                <DialogDescription>{t("auth.loginDescription")}</DialogDescription>
              </DialogHeader>
              <div
                className={styles.methodList}
                ref={methodListRef}
                onPointerMove={(event) => {
                  if (event.pointerType === "touch") return;
                  const button = methodFromTarget(event.target);
                  moveMethodLens(button);
                  if (button) pointLight(methodLensRef.current, button.getBoundingClientRect(), event.clientX, event.clientY);
                }}
                onPointerLeave={(event) => {
                  if (event.pointerType === "touch") return;
                  moveMethodLens(null);
                  resetLight(methodLensRef.current);
                  methodLensRef.current?.classList.remove(glassStyles.pressed);
                }}
                onPointerDown={(event) => {
                  const button = methodFromTarget(event.target);
                  if (!button) return;
                  moveMethodLens(button);
                  pointLight(methodLensRef.current, button.getBoundingClientRect(), event.clientX, event.clientY);
                  methodLensRef.current?.classList.add(glassStyles.pressed);
                }}
                onFocusCapture={(event) => moveMethodLens(methodFromTarget(event.target))}
                onBlurCapture={(event) => {
                  if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
                  moveMethodLens(null);
                  resetLight(methodLensRef.current);
                }}
              >
                <div className={`${glassStyles.lens} ${styles.methodLens}`} ref={methodLensRef} aria-hidden="true" />
                <Button data-login-method variant="ghost" className={styles.methodButton} onClick={handleGitHubLogin} disabled={isAnyLoginLoading}>
                  {isGitHubLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Github className="mr-2 h-4 w-4" />}
                  {t("auth.githubLogin")}
                </Button>
                <Button data-login-method variant="ghost" className={styles.methodButton} onClick={handleGoogleLogin} disabled={isAnyLoginLoading}>
                  {isGoogleLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Mail className="mr-2 h-4 w-4" />}
                  {t("auth.googleLogin")}
                </Button>
                {emailCapabilities.headless ? (
                  <Button data-login-method variant="ghost" className={styles.methodButton} onClick={handleEmailLogin} disabled={isAnyLoginLoading}>
                    <KeyRound className="mr-2 h-4 w-4" />
                    {t("auth.emailCodeLogin")}
                  </Button>
                ) : null}
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
