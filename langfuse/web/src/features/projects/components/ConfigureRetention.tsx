import { Card } from "@/src/components/ui/card";
import { Input } from "@/src/components/ui/input";
import { api } from "@/src/utils/api";
import type * as z from "zod/v4";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/src/components/ui/form";
import Header from "@/src/components/layouts/header";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { LockIcon } from "lucide-react";
import { useQueryProject } from "@/src/features/projects/hooks";
import { useSession } from "next-auth/react";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { projectRetentionSchema } from "@/src/features/auth/lib/projectRetentionSchema";
import { ActionButton } from "@/src/components/ActionButton";
import { useHasEntitlement } from "@/src/features/entitlements/hooks";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function ConfigureRetention() {
  const { update: updateSession } = useSession();
  const { project } = useQueryProject();
  const capture = usePostHogClientCapture();
  const { language } = useLanguage();
  const hasAccess = useHasProjectAccess({
    projectId: project?.id,
    scope: "project:update",
  });
  const hasEntitlement = useHasEntitlement("data-retention");

  const form = useForm({
    resolver: zodResolver(projectRetentionSchema),
    defaultValues: {
      retention: project?.retentionDays ?? 0,
    },
  });
  const setRetention = api.projects.setRetention.useMutation({
    onSuccess: (_) => {
      void updateSession();
    },
    onError: (error) => form.setError("retention", { message: error.message }),
  });

  function onSubmit(values: z.infer<typeof projectRetentionSchema>) {
    if (!hasAccess || !project) return;
    capture("project_settings:retention_form_submit");
    setRetention
      .mutateAsync({
        projectId: project.id,
        retention: values.retention || null, // Fallback to null for indefinite retention
      })
      .then(() => {
        form.reset();
      })
      .catch((error) => {
        console.error(error);
      });
  }

  return (
    <div>
      <Header title={localize(language, "Data Retention", "数据保留")} />
      <Card className="mb-4 p-3">
        <p className="mb-4 text-sm text-primary">
          {localize(
            language,
            "Data retention automatically deletes events older than the specified number of days. The value must be 0 or at least 3 days. Set to 0 to retain data indefinitely. The deletion happens asynchronously, i.e. events may be available for a while after they expire.",
            "数据保留会自动删除早于指定天数的事件。该值必须为 0 或至少 3 天。设置为 0 表示无限期保留数据。删除是异步进行的，因此事件在过期后一段时间内仍可能可见。",
          )}
        </p>
        {Boolean(form.getValues().retention) &&
        form.getValues().retention !== project?.retentionDays ? (
          <p className="mb-4 text-sm text-primary">
            {localize(
              language,
              "Your Project's retention will be set from \"",
              '你的项目保留期将从 "',
            )}
            {project?.retentionDays ??
              localize(language, "Indefinite", "无限期")}
            {localize(language, '" to "', '" 调整为 "')}
            {Number(form.watch("retention")) === 0
              ? localize(language, "Indefinite", "无限期")
              : Number(form.watch("retention"))}
            {localize(language, '" days.', '" 天。')}
          </p>
        ) : !Boolean(project?.retentionDays) ? (
          <p className="mb-4 text-sm text-primary">
            {localize(
              language,
              "Your Project retains data indefinitely.",
              "你的项目将无限期保留数据。",
            )}
          </p>
        ) : (
          <p className="mb-4 text-sm text-primary">
            {localize(
              language,
              "Your Project's current retention is \"",
              '你的项目当前保留期为 "',
            )}
            {project?.retentionDays ?? ""}
            {localize(language, '" days.', '" 天。')}
          </p>
        )}
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex-1"
            id="set-retention-project-form"
          >
            <FormField
              control={form.control}
              name="retention"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <div className="relative">
                      <Input
                        type="number"
                        step="1"
                        placeholder={project?.retentionDays?.toString() ?? ""}
                        {...field}
                        value={(field.value as number) ?? ""}
                        className="flex-1"
                        disabled={!hasAccess || !hasEntitlement}
                      />
                      {!hasAccess && (
                        <span
                          title={localize(language, "No access", "无访问权限")}
                        >
                          <LockIcon className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 transform text-muted" />
                        </span>
                      )}
                    </div>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <ActionButton
              variant="secondary"
              hasAccess={hasAccess}
              hasEntitlement={hasEntitlement}
              loading={setRetention.isPending}
              disabled={form.getValues().retention === null}
              className="mt-4"
              type="submit"
            >
              {localize(language, "Save", "保存")}
            </ActionButton>
          </form>
        </Form>
      </Card>
    </div>
  );
}
