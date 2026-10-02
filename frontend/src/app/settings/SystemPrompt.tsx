"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Check, Sparkles, Save, RotateCcw, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { SettingsButton } from "@/components/settings/SettingsControls";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import GlassHoverLens, { pointGlassLight, resetGlassLight } from "@/components/ui/GlassHoverLens";
import glassSurface from "@/components/ui/GlassSurface.module.css";
import styles from "@/components/settings/SettingsSurface.module.css";
import { cn } from "@/lib/utils";
import { useAppDispatch, useAppSelector } from "@/redux/hooks";
import { updateUserSystemPrompt } from "@/redux/slices/authSlice";

const MAX_LENGTH = 1000;

const TEMPLATE_KEYS = ["engineer", "writing", "learner"] as const;

export default function SystemPrompt() {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const savedPrompt = useAppSelector((state) => state.auth.user?.system_prompt ?? "");
  const fieldId = useId();
  const savingRef = useRef(false);

  const [draft, setDraft] = useState(savedPrompt);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<"success" | "error" | null>(null);

  useEffect(() => {
    setDraft(savedPrompt);
  }, [savedPrompt]);

  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => setFeedback(null), 3000);
    return () => clearTimeout(timer);
  }, [feedback]);

  const dirty = draft !== savedPrompt;
  const overLimit = draft.length > MAX_LENGTH;

  const handleSave = async () => {
    if (!dirty || overLimit || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setFeedback(null);
    try {
      await dispatch(updateUserSystemPrompt(draft)).unwrap();
      setFeedback("success");
    } catch {
      setFeedback("error");
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (savingRef.current) return;
    setDraft(savedPrompt);
    setFeedback(null);
  };

  const handleTemplate = (content: string) => {
    if (savingRef.current) return;
    setDraft(content);
    setFeedback(null);
  };

  return (
    <Card className="overflow-hidden border-border/70 shadow-none">
      <CardHeader className="border-b border-border/70 pb-5">
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-primary" aria-hidden="true" />
          {t("settings.personalization.title")}
        </CardTitle>
        <p className="text-sm text-muted-foreground mt-1">
          {t("settings.personalization.description")}
        </p>
      </CardHeader>
      <CardContent className={styles.editorContent}>
        <div>
          <p className="text-sm font-medium mb-3">{t("settings.personalization.templates")}</p>
          <div className={styles.templateGrid}>
            {TEMPLATE_KEYS.map((key) => {
              const content = t(`settings.personalization.template.${key}.content`);
              const selected = draft === content;
              return (
                <button
                  key={key}
                  type="button"
                  className={cn(glassSurface.surface, glassSurface.card, glassSurface.interactive, styles.templateButton, selected && glassSurface.selected)}
                  aria-label={t(`settings.personalization.template.${key}.label`)}
                  aria-pressed={selected}
                  aria-describedby={`${fieldId}-${key}`}
                  disabled={saving}
                  onClick={() => handleTemplate(content)}
                  onPointerMove={pointGlassLight}
                  onPointerLeave={resetGlassLight}
                >
                  <GlassHoverLens />
                  <span className={styles.templateCopy}>
                    <span className="flex items-center justify-between gap-2 font-medium">
                      {t(`settings.personalization.template.${key}.label`)}
                      <Check className={cn("h-4 w-4 text-primary", !selected && "invisible")} aria-hidden="true" />
                    </span>
                    <span id={`${fieldId}-${key}`} className={styles.templateDescription}>
                      {t(`settings.personalization.template.${key}.description`)}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className={styles.editorField}>
          <label htmlFor={fieldId} className="text-sm font-medium">{t("settings.personalization.label")}</label>
          <textarea
            id={fieldId}
            value={draft}
            onChange={(e) => {
              if (savingRef.current) return;
              setDraft(e.target.value);
              setFeedback(null);
            }}
            rows={8}
            disabled={saving}
            aria-invalid={overLimit}
            aria-describedby={`${fieldId}-hint ${fieldId}-count${overLimit ? ` ${fieldId}-error` : ""}`}
            placeholder={t("settings.personalization.placeholder")}
            className={styles.editorTextarea}
          />
          <p id={`${fieldId}-hint`} className="text-xs text-muted-foreground">{t("settings.personalization.hint")}</p>
          <div className={styles.editorMeta}>
            <span id={`${fieldId}-count`} className={styles.feedback} data-state={overLimit ? "error" : undefined}>
              {draft.length} / {MAX_LENGTH}
            </span>
            <span className="text-muted-foreground">{t(`settings.personalization.${dirty ? "unsaved" : "unchanged"}`)}</span>
          </div>
          {overLimit && <p id={`${fieldId}-error`} className={styles.feedback} data-state="error" role="alert">{t("settings.personalization.overLimit", { limit: MAX_LENGTH })}</p>}
          <p className={styles.feedback} data-state={feedback ?? undefined} role={feedback === "error" ? "alert" : "status"} aria-live={feedback === "error" ? "assertive" : "polite"}>
            {saving ? t("settings.personalization.saving") : feedback ? t(`settings.personalization.${feedback === "success" ? "saved" : "saveFailed"}`) : ""}
          </p>
        </div>

        <div className={styles.actions}>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={cn(glassSurface.surface, glassSurface.pill, glassSurface.interactive, styles.quietButton)}
            onClick={handleReset}
            onPointerMove={pointGlassLight}
            onPointerLeave={resetGlassLight}
            disabled={!dirty || saving}
          >
            <GlassHoverLens />
            <RotateCcw className="h-4 w-4" aria-hidden="true" />
            <span>{t("settings.personalization.reset")}</span>
          </Button>
          <SettingsButton
            type="button"
            size="sm"
            onClick={handleSave}
            disabled={!dirty || overLimit || saving}
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" aria-hidden="true" />
            )}
            {t(`settings.personalization.${saving ? "saving" : "save"}`)}
          </SettingsButton>
        </div>
      </CardContent>
    </Card>
  );
}
